"""Submission endpoints: queue a judging run, or execute custom input.

Nothing blocks the event loop: queueing is non-blocking, and the custom-run
wait uses a worker thread. Hidden test inputs/expected outputs are never part
of any response; hidden-test actual outputs are masked as well.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_actor, get_db, pagination_params
from app.core.auth import Actor
from app.core.config import get_settings
from app.core.exceptions import (
    NotFoundError,
    QueueFullError,
    SourceTooLargeError,
    ValidationFailedError,
)
from app.core.constants import Language, SubmissionStatus
from app.models.problem import Problem
from app.models.submission import Submission
from app.models.test_result import TestResult
from app.repositories import submission_repository as sub_repo
from app.schemas.common import Page, build_page
from app.schemas.submission import (
    RunRequest,
    SubmissionAccepted,
    SubmissionDetail,
    SubmissionSummary,
    SubmitRequest,
    TestResultPublic,
)
from app.services.execution_queue import execution_queue

router = APIRouter(prefix="/submissions", tags=["submissions"])


def _summary(sub: Submission) -> SubmissionSummary:
    return SubmissionSummary(
        id=sub.id,
        problem_id=sub.problem_id,
        language=sub.language,
        status=sub.status,
        workspace_label=sub.workspace_label,
        is_custom_run=sub.is_custom_run,
        execution_time_ms=sub.execution_time_ms,
        cpu_time_ms=sub.cpu_time_ms,
        peak_memory_bytes=sub.peak_memory_bytes,
        exit_code=sub.exit_code,
        error_summary=sub.error_summary,
        created_at=sub.created_at,
        completed_at=sub.completed_at,
    )


def _test_results(session: Session, submission_id: int) -> list[TestResultPublic]:
    rows = list(
        session.scalars(
            select(TestResult)
            .where(TestResult.submission_id == submission_id)
            .order_by(TestResult.test_index)
        )
    )
    out: list[TestResultPublic] = []
    for r in rows:
        hidden = not r.test_is_public
        out.append(
            TestResultPublic(
                id=r.id,
                test_index=r.test_index,
                test_is_public=r.test_is_public,
                test_is_generated=r.test_is_generated,
                status=r.status,
                execution_time_ms=r.execution_time_ms,
                memory_bytes=r.memory_bytes,
                exit_code=r.exit_code,
                error_summary=r.error_summary,
                # Actual output of a hidden test can reveal the expected
                # answer, so it is never returned to the client.
                output_summary=None if hidden else r.output_summary,
            )
        )
    return out


def _detail(session: Session, sub: Submission) -> SubmissionDetail:
    base = _summary(sub)
    return SubmissionDetail(
        **base.model_dump(),
        source_code=sub.source_code,
        compile_output=sub.compile_output,
        test_results=_test_results(session, sub.id),
    )


def _validate_source(source_code: str) -> None:
    settings = get_settings()
    size = len(source_code.encode("utf-8"))
    if size > settings.max_source_size_bytes:
        raise SourceTooLargeError(
            f"Source code is {size} bytes; the server limit is "
            f"{settings.max_source_size_bytes} bytes."
        )


def _enqueue(session: Session, sub: Submission) -> None:
    """Commit first (worker threads read committed rows), then enqueue."""
    session.commit()
    try:
        execution_queue.submit(sub.id)
    except QueueFullError:
        sub.status = SubmissionStatus.CANCELLED.value
        sub.error_summary = "Not executed: the execution queue was full."
        session.commit()
        raise


@router.post(
    "",
    response_model=SubmissionAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit source code for judging against a problem's tests",
)
def create_submission(
    payload: SubmitRequest,
    response: Response,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> SubmissionAccepted:
    _validate_source(payload.source_code)
    problem = session.get(Problem, payload.problem_id)
    if problem is None:
        raise NotFoundError(f"Problem {payload.problem_id} does not exist.")
    supported = list(problem.supported_languages or [])
    if supported and payload.language.value not in supported:
        raise ValidationFailedError(
            f"Problem {payload.problem_id} does not accept {payload.language.value} "
            f"submissions (supported: {', '.join(supported)}).",
            details={"field": "language"},
        )

    workspace_label = payload.workspace_label or actor.workspace_label
    sub = sub_repo.create_submission(
        session,
        problem_id=problem.id,
        language=payload.language.value,
        source_code=payload.source_code,
        workspace_label=workspace_label,
        is_custom_run=False,
    )
    _enqueue(session, sub)
    response.headers["Location"] = f"/api/v1/submissions/{sub.id}"
    return SubmissionAccepted(
        id=sub.id,
        status=sub.status,
        detail_url=f"/api/v1/submissions/{sub.id}",
    )


@router.post(
    "/run",
    response_model=SubmissionDetail,
    summary="Execute source code with custom stdin (no test evaluation)",
)
async def run_custom(
    payload: RunRequest,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> SubmissionDetail:
    """Compile and run once with the provided stdin.

    The request waits (in a worker thread, not on the event loop) up to
    ``RUN_WAIT_TIMEOUT_SECONDS`` for completion so the caller normally gets
    stdout in ``test_results[0].output_summary``. If judging takes longer,
    the response simply carries the current (not yet final) status and the
    client can poll ``GET /submissions/{id}``. Custom runs never affect the
    trust score.
    """
    settings = get_settings()
    _validate_source(payload.source_code)
    sub = sub_repo.create_submission(
        session,
        problem_id=None,
        language=payload.language.value,
        source_code=payload.source_code,
        workspace_label=payload.workspace_label or actor.workspace_label,
        is_custom_run=True,
    )
    sub.custom_input = payload.input
    session.flush()
    _enqueue(session, sub)

    await asyncio.to_thread(
        execution_queue.wait_for_completion,
        sub.id,
        settings.run_wait_timeout_seconds,
    )
    session.expire_all()
    fresh = sub_repo.get_submission(session, sub.id)
    if fresh is None:  # pragma: no cover - row was just created
        raise NotFoundError("The run disappeared before it could be read.")
    return _detail(session, fresh)


@router.get("", response_model=Page[SubmissionSummary], summary="List submissions")
def list_submissions(
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    problem_id: int | None = Query(None, ge=1),
    workspace_label: str | None = Query(
        None,
        description="Defaults to the caller's workspace label; pass explicitly "
        "to query another label (faculty surface, no role enforcement yet).",
    ),
) -> Page[SubmissionSummary]:
    rows, total = sub_repo.list_submissions(
        session,
        offset=(page - 1) * page_size,
        limit=page_size,
        status=status_filter,
        problem_id=problem_id,
        workspace_label=workspace_label or actor.workspace_label,
    )
    return build_page([_summary(r) for r in rows], total, page, page_size)


@router.get("/{submission_id}", response_model=SubmissionDetail, summary="Submission detail")
def get_submission(
    submission_id: int, session: Session = Depends(get_db)
) -> SubmissionDetail:
    sub = sub_repo.get_submission(session, submission_id)
    if sub is None:
        raise NotFoundError(f"Submission {submission_id} does not exist.")
    return _detail(session, sub)
