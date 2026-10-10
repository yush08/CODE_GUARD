"""Faculty endpoints: dashboard, submissions, alerts, review, trust history.

ROLE NOTE (local-dev mode): these endpoints are conceptually faculty/admin
surfaces, but no credential or role verification exists in this build — the
server is intended to run bound to 127.0.0.1. Real RBAC is future work and is
listed honestly in the README feature checklist.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_actor, get_db
from app.core.auth import Actor
from app.core.constants import (
    INITIAL_TRUST_SCORE,
    MIN_MONITORING_WITH_OPEN_ALERT,
    TRUST_ELEVATED_THRESHOLD,
    TRUST_FACTORS,
    TRUST_NORMAL_THRESHOLD,
    TRUST_SCORE_MAX,
    TRUST_SCORE_MIN,
    ReviewStatus,
)
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.models.security_alert import SecurityAlert
from app.models.submission import Submission
from app.models.trust_history import TrustHistory
from app.repositories import alert_repository, submission_repository as sub_repo
from app.repositories import stats_repository as stats
from app.schemas.common import Page, build_page
from app.schemas.faculty import (
    AlertDetail,
    AlertReviewRequest,
    AlertSummary,
    DashboardResponse,
    TrustHistoryEntry,
    TrustHistoryResponse,
)
from app.schemas.submission import SubmissionSummary
from app.services import trust_service
from app.services.report_service import build_workspace_report

router = APIRouter(prefix="/faculty", tags=["faculty"])


def _submission_summary(sub: Submission) -> SubmissionSummary:
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


def _alert_summary(a: SecurityAlert) -> AlertSummary:
    return AlertSummary(
        id=a.id,
        submission_id=a.submission_id,
        alert_type=a.alert_type,
        severity=a.severity,
        review_status=a.review_status,
        explanation=a.explanation,
        created_at=a.created_at,
    )


def _alert_detail(a: SecurityAlert) -> AlertDetail:
    return AlertDetail(
        **_alert_summary(a).model_dump(),
        evidence_json=a.evidence_json or {},
        reviewed_by=a.reviewed_by,
        review_note=a.review_note,
        reviewed_at=a.reviewed_at,
    )


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    summary="Aggregated dashboard (real stored rows only)",
)
def dashboard(session: Session = Depends(get_db)) -> DashboardResponse:
    submission_counts = stats.submission_status_counts(session)
    alert_counts = stats.alert_counts(session)
    coverage = stats.metric_coverage(session)
    recent_subs = [_submission_summary(s) for s in stats.recent_submissions(session, 10)]
    recent_alerts = [_alert_summary(a) for a in stats.recent_alerts(session, 10)]
    total = sum(submission_counts.values())
    return DashboardResponse(
        generated_at=datetime.now(timezone.utc),
        submissions={
            "total": total,
            "by_status": submission_counts,
            "accepted": submission_counts.get("ACCEPTED", 0),
            "accepted_rate_percent": (
                round(submission_counts.get("ACCEPTED", 0) / total * 100, 1)
                if total
                else 0.0
            ),
        },
        alerts=alert_counts,
        resource_metric_coverage=coverage,
        recent_submissions=recent_subs,
        recent_alerts=recent_alerts,
    )


# ---------------------------------------------------------------------------
# Submissions (faculty view: all workspace labels)
# ---------------------------------------------------------------------------
@router.get(
    "/submissions",
    response_model=Page[SubmissionSummary],
    summary="All submissions across workspace labels",
)
def list_submissions(
    session: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    problem_id: int | None = Query(None, ge=1),
    workspace_label: str | None = Query(None),
) -> Page[SubmissionSummary]:
    rows, total = sub_repo.list_submissions(
        session,
        offset=(page - 1) * page_size,
        limit=page_size,
        status=status_filter,
        problem_id=problem_id,
        workspace_label=workspace_label,
    )
    return build_page([_submission_summary(r) for r in rows], total, page, page_size)


# ---------------------------------------------------------------------------
# Alerts + review workflow
# ---------------------------------------------------------------------------
@router.get("/alerts", response_model=Page[AlertSummary], summary="Security alerts")
def list_alerts(
    session: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    severity: str | None = Query(None),
    review_status: str | None = Query(None),
    submission_id: int | None = Query(None, ge=1),
) -> Page[AlertSummary]:
    rows, total = alert_repository.list_alerts(
        session,
        offset=(page - 1) * page_size,
        limit=page_size,
        severity=severity,
        review_status=review_status,
        submission_id=submission_id,
    )
    return build_page([_alert_summary(a) for a in rows], total, page, page_size)


@router.get("/alerts/{alert_id}", response_model=AlertDetail, summary="Alert detail")
def get_alert(alert_id: int, session: Session = Depends(get_db)) -> AlertDetail:
    alert = alert_repository.get_alert(session, alert_id)
    if alert is None:
        raise NotFoundError(f"Alert {alert_id} does not exist.")
    return _alert_detail(alert)


@router.patch(
    "/alerts/{alert_id}",
    response_model=AlertDetail,
    summary="Record a faculty review decision on an alert",
)
def review_alert(
    alert_id: int,
    payload: AlertReviewRequest,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> AlertDetail:
    """Set the review status of an alert.

    CONFIRMED records a trust penalty and DISMISSED records a restoration for
    the submission's workspace label (at most once each, so repeated PATCH
    calls never double-count). UNDER_REVIEW / PENDING change only the status.
    """
    alert = alert_repository.get_alert(session, alert_id)
    if alert is None:
        raise NotFoundError(f"Alert {alert_id} does not exist.")
    try:
        ReviewStatus(payload.review_status)
    except ValueError as exc:  # pragma: no cover - pydantic already validates
        raise ValidationFailedError("Unknown review status.") from exc

    reviewed_by = payload.reviewed_by or actor.workspace_label
    alert_repository.review_alert(
        session,
        alert,
        review_status=payload.review_status.value,
        reviewed_by=reviewed_by,
        review_note=payload.review_note,
    )

    if payload.review_status.value in ("CONFIRMED", "DISMISSED"):
        submission = sub_repo.get_submission(session, alert.submission_id)
        label = submission.workspace_label if submission else None
        if label:
            try:
                trust_service.record_faculty_decision(
                    session, label, payload.review_status.value, alert.submission_id
                )
            except Exception:  # pragma: no cover - trust never fails review
                import logging

                logging.getLogger(__name__).exception(
                    "Trust update failed for alert %s", alert_id
                )
    session.flush()
    return _alert_detail(alert)


# ---------------------------------------------------------------------------
# Trust history
# ---------------------------------------------------------------------------
def _formula() -> dict:
    return {
        "initial_score": INITIAL_TRUST_SCORE,
        "range": [TRUST_SCORE_MIN, TRUST_SCORE_MAX],
        "factors": {
            code: {"delta": f.delta, "description": f.description}
            for code, f in sorted(TRUST_FACTORS.items())
        },
        "monitoring_levels": {
            "NORMAL": f"score >= {TRUST_NORMAL_THRESHOLD}",
            "ELEVATED": (
                f"{TRUST_ELEVATED_THRESHOLD} <= score < {TRUST_NORMAL_THRESHOLD}"
            ),
            "HIGH": f"score < {TRUST_ELEVATED_THRESHOLD}",
            "open_alert_minimum": MIN_MONITORING_WITH_OPEN_ALERT,
        },
        "dedupe": "one entry per (workspace_label, submission, factor)",
        "enforcement_independence": (
            "Trust never feeds back into resource limits or execution policy."
        ),
    }


@router.get(
    "/trust-history",
    response_model=TrustHistoryResponse,
    summary="Auditable trust history for one workspace label",
)
def trust_history(
    session: Session = Depends(get_db),
    workspace_label: str = Query(..., min_length=1, max_length=120),
    limit: int = Query(50, ge=1, le=200),
) -> TrustHistoryResponse:
    snap = trust_service.snapshot(session, workspace_label)
    rows = list(
        session.scalars(
            select(TrustHistory)
            .where(TrustHistory.workspace_label == workspace_label)
            .order_by(TrustHistory.created_at.desc(), TrustHistory.id.desc())
            .limit(limit)
        )
    )
    return TrustHistoryResponse(
        workspace_label=snap.workspace_label,
        current_score=snap.score,
        monitoring_level=snap.monitoring_level,
        open_alerts=snap.open_alerts,
        history_entries=snap.history_entries,
        formula=_formula(),
        entries=[
            TrustHistoryEntry(
                id=r.id,
                workspace_label=r.workspace_label,
                previous_score=r.previous_score,
                new_score=r.new_score,
                reason=r.reason,
                related_submission_id=r.related_submission_id,
                created_at=r.created_at,
            )
            for r in rows
        ],
    )


# ---------------------------------------------------------------------------
# Workspace report (aggregated, faculty-facing)
# ---------------------------------------------------------------------------
@router.get(
    "/reports/{workspace_label}",
    summary="Aggregated report for one workspace label",
)
def workspace_report(
    workspace_label: str, session: Session = Depends(get_db)
) -> dict:
    return build_workspace_report(session, workspace_label)
