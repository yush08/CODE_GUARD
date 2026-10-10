"""Submission persistence helpers (used by the execution worker and APIs)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.behavior_event import BehaviorEvent
from app.models.behavior_fingerprint import BehaviorFingerprint
from app.models.problem import Problem
from app.models.submission import Submission
from app.models.test_result import TestResult


def create_submission(
    session: Session,
    *,
    problem_id: int | None,
    language: str,
    source_code: str,
    workspace_label: str | None,
    is_custom_run: bool = False,
) -> Submission:
    submission = Submission(
        problem_id=problem_id,
        language=language,
        source_code=source_code,
        workspace_label=workspace_label,
        status="QUEUED",
        is_custom_run=is_custom_run,
    )
    session.add(submission)
    session.flush()
    return submission


def get_submission(session: Session, submission_id: int) -> Submission | None:
    return session.get(Submission, submission_id)


def list_submissions(
    session: Session,
    *,
    offset: int,
    limit: int,
    status: str | None = None,
    problem_id: int | None = None,
    workspace_label: str | None = None,
) -> tuple[list[Submission], int]:
    stmt = select(Submission)
    if status:
        stmt = stmt.where(Submission.status == status)
    if problem_id:
        stmt = stmt.where(Submission.problem_id == problem_id)
    if workspace_label:
        stmt = stmt.where(Submission.workspace_label == workspace_label)
    total = int(
        session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    )
    rows = list(
        session.execute(
            stmt.order_by(Submission.created_at.desc(), Submission.id.desc())
            .offset(offset)
            .limit(limit)
        ).scalars()
    )
    return rows, total


def set_status(session: Session, submission: Submission, status: str) -> None:
    submission.status = status
    session.flush()


def complete_submission(
    session: Session,
    submission: Submission,
    *,
    status: str,
    compile_output: str | None,
    execution_time_ms: int | None,
    cpu_time_ms: int | None,
    peak_memory_bytes: int | None,
    exit_code: int | None,
    error_summary: str | None,
) -> None:
    submission.status = status
    submission.compile_output = compile_output
    submission.execution_time_ms = execution_time_ms
    submission.cpu_time_ms = cpu_time_ms
    submission.peak_memory_bytes = peak_memory_bytes
    submission.exit_code = exit_code
    submission.error_summary = error_summary
    submission.completed_at = datetime.now(timezone.utc)
    session.flush()


def save_test_results(session: Session, submission: Submission, outcomes: Sequence[Any]) -> None:
    for outcome in outcomes:
        session.add(
            TestResult(
                submission_id=submission.id,
                test_case_id=outcome.test_case_id,
                test_index=outcome.test_index,
                test_is_public=outcome.is_public,
                test_is_generated=outcome.is_generated,
                status=outcome.status.value,
                execution_time_ms=outcome.run.duration_ms,
                memory_bytes=outcome.run.peak_memory_bytes,
                exit_code=outcome.run.exit_code,
                error_summary=outcome.error_summary,
                output_summary=outcome.output_summary,
                output_hash=outcome.output_hash,
            )
        )
    session.flush()


def save_events(session: Session, submission_id: int, rows: Sequence[dict[str, Any]]) -> None:
    for row in rows:
        session.add(BehaviorEvent(**row))
    session.flush()


def save_fingerprint(session: Session, submission_id: int, fingerprint: dict[str, Any]) -> None:
    existing = session.execute(
        select(BehaviorFingerprint).where(
            BehaviorFingerprint.submission_id == submission_id
        )
    ).scalar_one_or_none()
    source = fingerprint.get("measurement_source", "OBSERVED")
    if existing:
        existing.fingerprint_json = fingerprint
        existing.measurement_source = source
    else:
        session.add(
            BehaviorFingerprint(
                submission_id=submission_id,
                fingerprint_json=fingerprint,
                measurement_source=source,
            )
        )
    session.flush()


def problem_of(session: Session, submission: Submission) -> Problem | None:
    if submission.problem_id is None:
        return None
    return session.get(Problem, submission.problem_id)
