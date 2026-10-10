"""Security alert persistence (faculty review workflow)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.security_alert import SecurityAlert


def has_alert(session: Session, submission_id: int, alert_type: str) -> bool:
    return (
        session.execute(
            select(SecurityAlert.id).where(
                SecurityAlert.submission_id == submission_id,
                SecurityAlert.alert_type == alert_type,
            )
        ).first()
        is not None
    )


def create_alert(
    session: Session,
    *,
    submission_id: int,
    alert_type: str,
    severity: str,
    explanation: str,
    evidence: dict[str, Any],
) -> SecurityAlert | None:
    """Create an alert unless one of the same type already exists for the
    submission (idempotent re-analysis)."""
    if has_alert(session, submission_id, alert_type):
        return None
    alert = SecurityAlert(
        submission_id=submission_id,
        alert_type=alert_type,
        severity=severity,
        explanation=explanation,
        evidence_json=evidence,
        review_status="PENDING",
    )
    session.add(alert)
    session.flush()
    return alert


def get_alert(session: Session, alert_id: int) -> SecurityAlert | None:
    return session.get(SecurityAlert, alert_id)


def list_alerts(
    session: Session,
    *,
    offset: int,
    limit: int,
    severity: str | None = None,
    review_status: str | None = None,
    submission_id: int | None = None,
) -> tuple[list[SecurityAlert], int]:
    stmt = select(SecurityAlert)
    if severity:
        stmt = stmt.where(SecurityAlert.severity == severity)
    if review_status:
        stmt = stmt.where(SecurityAlert.review_status == review_status)
    if submission_id:
        stmt = stmt.where(SecurityAlert.submission_id == submission_id)
    total = int(
        session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    )
    rows = list(
        session.execute(
            stmt.order_by(SecurityAlert.created_at.desc(), SecurityAlert.id.desc())
            .offset(offset)
            .limit(limit)
        ).scalars()
    )
    return rows, total


def review_alert(
    session: Session,
    alert: SecurityAlert,
    *,
    review_status: str,
    reviewed_by: str | None,
    review_note: str | None,
) -> SecurityAlert:
    alert.review_status = review_status
    alert.reviewed_by = reviewed_by
    alert.review_note = review_note
    alert.reviewed_at = datetime.now(timezone.utc)
    session.flush()
    return alert
