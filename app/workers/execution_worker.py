"""Execution worker: the only place that runs submissions AND the database.

Worker threads pull submission ids from the bounded queue and run the full
pipeline:

1. load the submission and its problem's test cases,
2. run the DB-free judge pipeline (compile/execute/compare/analyze),
3. persist test results, runtime events, the behavior fingerprint,
4. create faculty-review alerts from sequence + hardcoding findings,
5. record auditable trust-score updates,
6. store the final status and resource metrics.

Database sessions are created per job (never shared between threads), and
every intermediate status transition that is persisted (QUEUED → COMPILING →
RUNNING → ANALYZING → final) is a real state the client can observe.

Custom-input runs (POST /submissions/run) execute the same pipeline but do
not influence the trust score: a debugging run is not a conduct signal.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

from app.core.constants import (
    EventType,
    SubmissionStatus,
)
from app.core.exceptions import CodeGuardError
from app.db.session import session_scope
from app.models.submission import Submission
from app.repositories import (
    alert_repository,
    submission_repository as sub_repo,
)
from app.services import sequence_analyzer, trust_service
from app.services.judge_service import (
    JudgeOutcome,
    ProblemLimits,
    TestCaseSpec,
    judge_submission,
)

logger = logging.getLogger(__name__)


def _load_test_specs(session, problem) -> list[TestCaseSpec]:
    from app.repositories.problem_repository import judge_test_cases

    cases = judge_test_cases(session, problem)
    return [
        TestCaseSpec(
            id=case.id,
            index=i + 1,
            input_data=case.input_data,
            expected_output=case.expected_output,
            is_public=case.is_public,
            is_generated=case.is_generated,
            label=case.label,
        )
        for i, case in enumerate(cases)
    ]


def _make_progress(session, submission: Submission) -> Callable[[SubmissionStatus], None]:
    def apply(status: SubmissionStatus) -> None:
        sub_repo.set_status(session, submission, status)
        session.commit()

    return apply


def _judge_details(session, submission: Submission, outcome: JudgeOutcome) -> dict:
    """Snapshot compilation / policy / enforcement / analysis for later APIs."""
    from app.core.config import get_settings

    settings = get_settings()
    compile_info: dict | None = None
    if outcome.compile is not None:
        c = outcome.compile
        compile_info = {
            "attempted": True,
            "success": c.success,
            "compiler": c.compiler.compiler,
            "compiler_version": c.compiler.version,
            "compiler_path": c.compiler.path,
            "flags": (
                settings.compiler_c_flags
                if submission.language == "c"
                else settings.compiler_cpp_flags
            ),
            "duration_ms": c.duration_ms,
            "failure_reason": c.failure_reason,
            "timed_out": c.timed_out,
            "output_truncated": c.output_truncated,
        }
    else:
        compile_info = {"attempted": False}

    first_run = outcome.tests[0].run if outcome.tests else None
    return {
        "compilation": compile_info,
        "policy": outcome.policy.as_dict(),
        "enforcement": (first_run.enforcement if first_run else {}),
        # Time spent inside CreateProcess (antivirus first-use analysis can
        # add seconds); excluded from the per-run execution time.
        "spawn_ms": (first_run.spawn_ms if first_run else None),
        "job": (
            {
                "available": first_run.job_available,
                "detail": first_run.job_detail,
            }
            if first_run
            else None
        ),
        "acl": outcome.acl_detail,
        "cleanup": {"ok": outcome.cleanup_ok, "detail": outcome.cleanup_detail},
        "workspace": outcome.workspace_name,
        "sequence_analysis": outcome.sequence_analysis,
        "hardcoding": outcome.hardcoding,
    }


def persist_outcome(session, submission: Submission, outcome: JudgeOutcome) -> None:
    """Store everything the judge produced (single transaction per phase)."""
    sub_repo.save_test_results(session, submission, outcome.tests)
    if outcome.monitor is not None:
        sub_repo.save_events(session, submission.id, outcome.monitor.to_rows(submission.id))
    sub_repo.save_fingerprint(session, submission.id, outcome.fingerprint)

    # -- alerts from sequence analysis --------------------------------------
    analysis = sequence_analyzer.analyze(outcome.monitor.events()) if outcome.monitor else None
    if analysis is not None:
        for match in sequence_analyzer.alertable_matches(analysis):
            alert_repository.create_alert(
                session,
                submission_id=submission.id,
                alert_type=match.rule_id,
                severity=sequence_analyzer.severity_for_risk(match.risk_level).value,
                explanation=match.explanation,
                evidence={
                    "analysis_type": "SEQUENCE_ANALYSIS",
                    "rule": match.rule_id,
                    "risk_level": match.risk_level.value,
                    "observed_events": match.matched_event_types,
                    "matched_events": match.evidence,
                    "source": "INFERRED",
                },
            )

    # -- alert from hardcoding heuristic ------------------------------------
    if outcome.hardcoding and outcome.hardcoding.get("status") == "REVIEW_RECOMMENDED":
        alert_repository.create_alert(
            session,
            submission_id=submission.id,
            alert_type="POSSIBLE_HARDCODING",
            severity="MEDIUM",
            explanation=outcome.hardcoding.get("explanation", "Review recommended."),
            evidence=outcome.hardcoding,
        )

    # -- trust updates (skipped for custom runs by design) -------------------
    if not submission.is_custom_run and submission.workspace_label:
        try:
            trust_service.record_factors(
                session,
                submission.workspace_label,
                outcome.trust_factors,
                submission.id,
            )
        except Exception:  # pragma: no cover - trust must never fail judging
            logger.exception("Trust update failed for submission %s", submission.id)

    sub_repo.complete_submission(
        session,
        submission,
        status=outcome.status.value,
        compile_output=outcome.compile.output if outcome.compile else None,
        execution_time_ms=outcome.fingerprint["fingerprint"].get("execution_time_ms"),
        cpu_time_ms=outcome.fingerprint["fingerprint"].get("cpu_time_ms"),
        peak_memory_bytes=outcome.fingerprint["fingerprint"].get("peak_memory_bytes"),
        exit_code=outcome.fingerprint["fingerprint"].get("exit_code"),
        error_summary=outcome.error_summary,
    )
    submission.judge_details_json = _judge_details(session, submission, outcome)


def process_submission(submission_id: int) -> None:
    """Full pipeline for one queued submission. Never raises."""
    try:
        with session_scope() as session:
            submission = sub_repo.get_submission(session, submission_id)
            if submission is None:
                logger.warning("Submission %s not found; skipping.", submission_id)
                return
            problem = sub_repo.problem_of(session, submission)

            if submission.is_custom_run:
                specs = [
                    TestCaseSpec(
                        id=None,
                        index=0,
                        input_data=submission.custom_input or "",
                        expected_output=None,
                        is_public=True,
                    )
                ]
            else:
                if problem is None:
                    sub_repo.complete_submission(
                        session,
                        submission,
                        status=SubmissionStatus.INTERNAL_ERROR.value,
                        compile_output=None,
                        execution_time_ms=None,
                        cpu_time_ms=None,
                        peak_memory_bytes=None,
                        exit_code=None,
                        error_summary="Problem no longer exists for this submission.",
                    )
                    return
                specs = _load_test_specs(session, problem)
                if not specs:
                    sub_repo.complete_submission(
                        session,
                        submission,
                        status=SubmissionStatus.INTERNAL_ERROR.value,
                        compile_output=None,
                        execution_time_ms=None,
                        cpu_time_ms=None,
                        peak_memory_bytes=None,
                        exit_code=None,
                        error_summary="The problem has no test cases configured.",
                    )
                    return

            limits = (
                ProblemLimits(
                    time_limit_ms=problem.time_limit_ms if problem else None,
                    memory_limit_mb=problem.memory_limit_mb if problem else None,
                    process_limit=problem.process_limit if problem else None,
                )
                if problem
                else None
            )
            outcome = judge_submission(
                submission.source_code,
                submission.language,
                specs,
                problem_limits=limits,
                workspace_label=submission.workspace_label or "local-workspace",
                submission_id=submission.id,
                on_progress=_make_progress(session, submission),
                enable_hardcoding_analysis=not submission.is_custom_run,
            )
            persist_outcome(session, submission, outcome)
    except CodeGuardError as exc:
        logger.error("Submission %s rejected by pipeline: %s", submission_id, exc.code)
        _mark_internal_error(submission_id, exc.message)
    except Exception:
        logger.exception("Submission %s failed with an internal error", submission_id)
        _mark_internal_error(submission_id, "Internal judge failure. See server logs.")


def _mark_internal_error(submission_id: int, summary: str) -> None:
    try:
        with session_scope() as session:
            submission = sub_repo.get_submission(session, submission_id)
            if submission is None:
                return
            sub_repo.complete_submission(
                session,
                submission,
                status=SubmissionStatus.INTERNAL_ERROR.value,
                compile_output=submission.compile_output,
                execution_time_ms=None,
                cpu_time_ms=None,
                peak_memory_bytes=None,
                exit_code=None,
                error_summary=summary,
            )
    except Exception:  # pragma: no cover
        logger.exception("Could not persist INTERNAL_ERROR for %s", submission_id)


def worker_loop(
    work_queue: "queue.Queue[int]",
    stop_event: threading.Event,
    on_done: Callable[[int], None],
) -> None:
    """Worker thread main loop."""
    while not stop_event.is_set():
        try:
            submission_id = work_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            process_submission(submission_id)
        finally:
            work_queue.task_done()
            try:
                on_done(submission_id)
            except Exception:  # pragma: no cover
                logger.exception("Completion notification failed for %s", submission_id)
