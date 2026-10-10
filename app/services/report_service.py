"""Aggregate submission report assembly (JSON for the separate frontend).

Everything here is built from stored rows: no HTML rendering happens in the
backend, and no value is invented when a measurement is missing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    BehaviorFingerprint,
    Problem,
    SecurityAlert,
    Submission,
    TestResult,
    TrustHistory,
)
from app.schemas.analysis import (
    ReportAlertItem,
    ReportAnalysis,
    ReportCompilation,
    ReportResponse,
    ReportTestSummary,
    ReportTrust,
)
from app.schemas.submission import SubmissionSummary, TestResultPublic
from app.services.trust_service import monitoring_level, open_alert_count

#: Explicit, honest limitations shown in every report.
REPORT_LIMITATIONS = [
    "Windows Job Objects enforce the wall-clock deadline and the output cap; "
    "the memory limit is enforced as a job-wide commit cap "
    "(JOB_OBJECT_LIMIT_JOB_MEMORY) and the active-process ceiling as "
    "(JOB_OBJECT_LIMIT_ACTIVE_PROCESS) when Job Objects are available. "
    "CPU usage is MEASURED only: a kernel per-job CPU-time cap is configured "
    "but was verified unreliable on this machine, so CPU-bound programs are "
    "stopped by the wall-clock deadline. Per-limit states are listed in "
    "resource_measurements.enforcement.",
    "Job Objects are not a complete sandbox: there is no filesystem or "
    "network virtualization. Isolation is per-run workspace directories plus "
    "deny-ACL hardening where the OS allows it.",
    "Metrics that could not be measured are null and listed as UNAVAILABLE; "
    "null never means zero.",
    "Sequence and hardcoding results are assistive signals for human review, "
    "not proof of misconduct. Hardcoding confidence is always null by design.",
    "Trust/monitoring scores are scoped to a local workspace label on this "
    "machine; a label is not a verified student identity.",
]


def _public_test_result(result: TestResult, *, mask_hidden_output: bool) -> TestResultPublic:
    hidden = not result.test_is_public
    return TestResultPublic(
        id=result.id,
        test_index=result.test_index,
        test_is_public=result.test_is_public,
        test_is_generated=result.test_is_generated,
        status=result.status,
        execution_time_ms=result.execution_time_ms,
        memory_bytes=result.memory_bytes,
        exit_code=result.exit_code,
        error_summary=result.error_summary,
        # Hidden-test actual output can reveal the expected answer, so it is
        # masked unless the caller is explicitly allowed to see it.
        output_summary=(
            None
            if (hidden and mask_hidden_output)
            else (result.output_summary[:4000] if result.output_summary else None)
        ),
    )


def build_submission_report(
    session: Session,
    submission: Submission,
    *,
    include_hidden_outputs: bool = False,
) -> ReportResponse:
    """Assemble the full stored record for one submission."""
    problem = session.get(Problem, submission.problem_id) if submission.problem_id else None
    details: dict[str, Any] = submission.judge_details_json or {}

    fingerprint = session.scalar(
        select(BehaviorFingerprint).where(
            BehaviorFingerprint.submission_id == submission.id
        )
    )
    alerts = list(
        session.scalars(
            select(SecurityAlert)
            .where(SecurityAlert.submission_id == submission.id)
            .order_by(SecurityAlert.created_at.asc())
        )
    )
    results = list(
        session.scalars(
            select(TestResult)
            .where(TestResult.submission_id == submission.id)
            .order_by(TestResult.test_index.asc())
        )
    )

    counts = {
        "total": len(results),
        "accepted": sum(1 for r in results if r.status == "ACCEPTED"),
        "wrong_answer": sum(1 for r in results if r.status == "WRONG_ANSWER"),
        "runtime_error": sum(1 for r in results if r.status == "RUNTIME_ERROR"),
        "time_limit_exceeded": sum(
            1 for r in results if r.status == "TIME_LIMIT_EXCEEDED"
        ),
        "memory_limit_exceeded": sum(
            1 for r in results if r.status == "MEMORY_LIMIT_EXCEEDED"
        ),
        "process_limit_exceeded": sum(
            1 for r in results if r.status == "PROCESS_LIMIT_EXCEEDED"
        ),
    }

    resource_measurements: dict[str, Any] = {
        "policy": details.get("policy"),
        "enforcement": details.get("enforcement"),
        "spawn_ms": details.get("spawn_ms"),
        "job": details.get("job"),
        "acl": details.get("acl"),
        "cleanup": details.get("cleanup"),
        "submission_metrics": {
            "execution_time_ms": submission.execution_time_ms,
            "cpu_time_ms": submission.cpu_time_ms,
            "peak_memory_bytes": submission.peak_memory_bytes,
            "exit_code": submission.exit_code,
        },
        "measurements": (fingerprint.fingerprint_json.get("measurements") if fingerprint else None),
        "note": (
            "enforcement states what the engine actually enforced for this "
            "run; measurements list per-metric MEASURED/UNAVAILABLE coverage; "
            "spawn_ms is time the OS spent creating the process (antivirus "
            "first-use analysis can add seconds) and is not execution time."
        ),
    }

    analysis = ReportAnalysis(
        fingerprint=(
            {
                "measurement_source": fingerprint.measurement_source,
                **fingerprint.fingerprint_json,
            }
            if fingerprint
            else None
        ),
        sequence_analysis=details.get("sequence_analysis"),
        hardcoding_analysis=details.get("hardcoding"),
    )

    trust_section = None
    if submission.workspace_label:
        from app.services.trust_service import current_score as trust_current_score

        score = trust_current_score(session, submission.workspace_label)
        open_alerts = open_alert_count(session, submission.workspace_label)
        trust_section = ReportTrust(
            workspace_label=submission.workspace_label,
            current_score=score,
            monitoring_level=monitoring_level(score, open_alerts),
            open_alerts=open_alerts,
        )

    return ReportResponse(
        generated_at=datetime.now(timezone.utc),
        submission=SubmissionSummary(
            id=submission.id,
            problem_id=submission.problem_id,
            language=submission.language,
            status=submission.status,
            workspace_label=submission.workspace_label,
            is_custom_run=submission.is_custom_run,
            execution_time_ms=submission.execution_time_ms,
            cpu_time_ms=submission.cpu_time_ms,
            peak_memory_bytes=submission.peak_memory_bytes,
            exit_code=submission.exit_code,
            error_summary=submission.error_summary,
            created_at=submission.created_at,
            completed_at=submission.completed_at,
        ),
        problem=(
            {
                "id": problem.id,
                "title": problem.title,
                "slug": problem.slug,
                "time_limit_ms": problem.time_limit_ms,
                "memory_limit_mb": problem.memory_limit_mb,
                "process_limit": problem.process_limit,
            }
            if problem
            else None
        ),
        compilation=_compilation(submission, details),
        test_summary=ReportTestSummary(
            **counts,
            results=[
                _public_test_result(r, mask_hidden_output=not include_hidden_outputs)
                for r in results
            ],
        ),
        resource_measurements=resource_measurements,
        analysis=analysis,
        alerts=[
            ReportAlertItem(
                id=a.id,
                alert_type=a.alert_type,
                severity=a.severity,
                review_status=a.review_status,
                explanation=a.explanation,
                created_at=a.created_at,
            )
            for a in alerts
        ],
        trust=trust_section,
        limitations=list(REPORT_LIMITATIONS),
    )


def _compilation(submission: Submission, details: dict[str, Any]) -> ReportCompilation:
    comp = details.get("compilation") or {}
    return ReportCompilation(
        attempted=bool(comp.get("attempted")),
        success=comp.get("success"),
        compiler=comp.get("compiler"),
        compiler_version=comp.get("compiler_version"),
        compiler_path=comp.get("compiler_path"),
        flags=comp.get("flags"),
        duration_ms=comp.get("duration_ms"),
        output=submission.compile_output,
        failure_reason=comp.get("failure_reason"),
    )


def build_workspace_report(
    session: Session, workspace_label: str
) -> dict[str, Any]:
    """Aggregate report for one local workspace label (faculty-facing)."""
    from app.services.trust_service import current_score as trust_current_score

    subs = list(
        session.scalars(
            select(Submission)
            .where(Submission.workspace_label == workspace_label)
            .order_by(Submission.created_at.desc())
        )
    )
    alerts = list(
        session.scalars(
            select(SecurityAlert)
            .join(Submission, Submission.id == SecurityAlert.submission_id)
            .where(Submission.workspace_label == workspace_label)
            .order_by(SecurityAlert.created_at.desc())
        )
    )
    history = list(
        session.scalars(
            select(TrustHistory)
            .where(TrustHistory.workspace_label == workspace_label)
            .order_by(TrustHistory.created_at.asc(), TrustHistory.id.asc())
        )
    )

    status_counts: dict[str, int] = {}
    for s in subs:
        status_counts[s.status] = status_counts.get(s.status, 0) + 1
    severity_counts: dict[str, int] = {}
    review_counts: dict[str, int] = {}
    for a in alerts:
        severity_counts[a.severity] = severity_counts.get(a.severity, 0) + 1
        review_counts[a.review_status] = review_counts.get(a.review_status, 0) + 1

    score = trust_current_score(session, workspace_label)
    open_alerts = open_alert_count(session, workspace_label)

    return {
        "workspace_label": workspace_label,
        "generated_at": datetime.now(timezone.utc),
        "submission_count": len(subs),
        "status_counts": status_counts,
        "alert_count": len(alerts),
        "alert_counts_by_severity": severity_counts,
        "review_status_counts": review_counts,
        "trust": {
            "current_score": score,
            "monitoring_level": monitoring_level(score, open_alerts),
            "open_alerts": open_alerts,
            "history_entries": len(history),
            "recent_entries": [
                {
                    "id": h.id,
                    "previous_score": h.previous_score,
                    "new_score": h.new_score,
                    "reason": h.reason,
                    "related_submission_id": h.related_submission_id,
                    "created_at": h.created_at,
                }
                for h in history[-20:]
            ],
        },
        "recent_submissions": [
            {
                "id": s.id,
                "problem_id": s.problem_id,
                "language": s.language,
                "status": s.status,
                "is_custom_run": s.is_custom_run,
                "created_at": s.created_at,
                "completed_at": s.completed_at,
            }
            for s in subs[:20]
        ],
        "note": (
            "Aggregates of stored rows for this local workspace label only. "
            "A workspace label is not a verified student identity, and this "
            "report covers this machine only."
        ),
    }
