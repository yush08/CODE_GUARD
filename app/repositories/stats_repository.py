"""Aggregate statistics for the faculty dashboard.

Every number is computed from real stored rows. When the database contains
no submissions, the dashboard returns genuine zeros and empty lists — never
fabricated demo data.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.behavior_fingerprint import BehaviorFingerprint
from app.models.security_alert import SecurityAlert
from app.models.submission import Submission


def submission_status_counts(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(Submission.status, func.count()).group_by(Submission.status)
    ).all()
    return {status: int(count) for status, count in rows}


def recent_submissions(session: Session, limit: int = 10) -> list[Submission]:
    return list(
        session.execute(
            select(Submission)
            .order_by(Submission.created_at.desc(), Submission.id.desc())
            .limit(limit)
        ).scalars()
    )


def alert_counts(session: Session) -> dict[str, int]:
    by_severity = dict(
        session.execute(
            select(SecurityAlert.severity, func.count()).group_by(SecurityAlert.severity)
        ).all()
    )
    pending = int(
        session.execute(
            select(func.count()).select_from(SecurityAlert).where(
                SecurityAlert.review_status == "PENDING"
            )
        ).scalar_one()
    )
    return {
        "total": sum(int(v) for v in by_severity.values()),
        "pending_review": pending,
        **{f"severity_{k.lower()}": int(v) for k, v in by_severity.items()},
    }


def recent_alerts(session: Session, limit: int = 10) -> list[SecurityAlert]:
    return list(
        session.execute(
            select(SecurityAlert)
            .order_by(SecurityAlert.created_at.desc(), SecurityAlert.id.desc())
            .limit(limit)
        ).scalars()
    )


def metric_coverage(session: Session) -> dict[str, int | float]:
    """How complete the resource measurements are across stored submissions."""
    total = int(session.execute(select(func.count()).select_from(Submission)).scalar_one())
    if total == 0:
        return {
            "submissions_total": 0,
            "with_cpu_time": 0,
            "with_peak_memory": 0,
            "with_fingerprint": 0,
            "coverage_cpu_percent": 0.0,
            "coverage_memory_percent": 0.0,
            "coverage_fingerprint_percent": 0.0,
        }
    with_cpu = int(
        session.execute(
            select(func.count()).select_from(Submission).where(Submission.cpu_time_ms.is_not(None))
        ).scalar_one()
    )
    with_mem = int(
        session.execute(
            select(func.count()).select_from(Submission).where(Submission.peak_memory_bytes.is_not(None))
        ).scalar_one()
    )
    with_fp = int(
        session.execute(select(func.count()).select_from(BehaviorFingerprint)).scalar_one()
    )
    return {
        "submissions_total": total,
        "with_cpu_time": with_cpu,
        "with_peak_memory": with_mem,
        "with_fingerprint": with_fp,
        "coverage_cpu_percent": round(with_cpu / total * 100, 1),
        "coverage_memory_percent": round(with_mem / total * 100, 1),
        "coverage_fingerprint_percent": round(with_fp / total * 100, 1),
    }


def dashboard(
    session: Session, *, recent_limit: int = 10
) -> dict:
    counts = submission_status_counts(session)
    total = sum(counts.values())
    accepted = counts.get("ACCEPTED", 0)
    alerts = alert_counts(session)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "submissions": {
            "total": total,
            "by_status": counts,
            "accepted": accepted,
            "wrong_answer": counts.get("WRONG_ANSWER", 0),
            "compilation_error": counts.get("COMPILATION_ERROR", 0),
            "runtime_error": counts.get("RUNTIME_ERROR", 0),
            "time_limit_exceeded": counts.get("TIME_LIMIT_EXCEEDED", 0),
            "memory_limit_exceeded": counts.get("MEMORY_LIMIT_EXCEEDED", 0),
            "process_limit_exceeded": counts.get("PROCESS_LIMIT_EXCEEDED", 0),
            "security_violation": counts.get("SECURITY_VIOLATION", 0),
            "internal_error": counts.get("INTERNAL_ERROR", 0),
        },
        "alerts": alerts,
        "resource_metric_coverage": metric_coverage(session),
    }
