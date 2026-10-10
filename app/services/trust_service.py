"""Novelty Feature 4 — adaptive trust (monitoring score).

An auditable, workspace-scoped monitoring score derived from recorded
execution outcomes and faculty decisions.

Scoring formula (see app.core.constants.TRUST_FACTORS):

* start score: 80 (range 0..100, higher = more trusted / less monitoring)
* +2 per clean ACCEPTED run (capped at 100)
* -5 time / memory / output limit violations
* -15 active-process limit violation, faculty-confirmed finding
* -25 HIGH-risk sequence alert, -10 MEDIUM-risk sequence alert
* +15 faculty-dismissed finding
* WRONG ANSWERS and COMPILATION ERRORS carry a factor of 0 — an ordinary
  buggy submission is never treated as malicious behavior.

Guarantees:

* One entry per (workspace_label, submission_id, factor): duplicates are
  skipped in application code AND blocked by a DB unique constraint.
* Scores are clamped to [0, 100]; every change is recorded with its reason.
* The score NEVER feeds back into execution policy. Resource and access
  controls are identical for every submission regardless of trust.
* A workspace label is a local grouping key, NOT a verified identity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.constants import (
    INITIAL_TRUST_SCORE,
    MIN_MONITORING_WITH_OPEN_ALERT,
    TRUST_ELEVATED_THRESHOLD,
    TRUST_FACTORS,
    TRUST_NORMAL_THRESHOLD,
    TRUST_SCORE_MAX,
    TRUST_SCORE_MIN,
    RiskLevel,
)
from app.models.trust_history import TrustHistory

logger = logging.getLogger(__name__)


def clamp_score(value: int) -> int:
    return max(TRUST_SCORE_MIN, min(TRUST_SCORE_MAX, int(value)))


def apply_factors(current: int, factors: Iterable[str]) -> tuple[int, list[dict[str, Any]]]:
    """Pure scoring function: apply known factors to a score.

    Unknown factor codes are ignored (and reported), never crash the judge.
    """
    score = clamp_score(current)
    applied: list[dict[str, Any]] = []
    for code in factors:
        factor = TRUST_FACTORS.get(code)
        if factor is None:
            applied.append({"factor": code, "delta": 0, "applied": False, "note": "unknown factor"})
            continue
        previous = score
        score = clamp_score(score + factor.delta)
        applied.append(
            {
                "factor": factor.factor,
                "delta": score - previous,
                "applied": score != previous,
                "description": factor.description,
            }
        )
    return score, applied


def monitoring_level(score: int, open_alerts: int = 0) -> str:
    """Derive the monitoring level. Open faculty alerts raise it to at least
    ELEVATED, independent of the numeric score."""
    if score >= TRUST_NORMAL_THRESHOLD:
        level = "NORMAL"
    elif score >= TRUST_ELEVATED_THRESHOLD:
        level = "ELEVATED"
    else:
        level = "HIGH"
    if open_alerts > 0 and level == "NORMAL":
        return MIN_MONITORING_WITH_OPEN_ALERT
    return level


def factors_for_judge_outcome(
    *,
    final_status: str,
    any_time_limit: bool,
    any_memory_limit: bool,
    any_process_limit: bool,
    any_output_limit: bool,
    sequence_risk: RiskLevel,
) -> list[str]:
    """Map a finished submission to its trust factors (pure, deterministic)."""
    factors: list[str] = []
    if any_process_limit:
        factors.append("PROCESS_LIMIT_VIOLATION")
    if any_time_limit:
        factors.append("TIME_LIMIT_VIOLATION")
    if any_memory_limit:
        factors.append("MEMORY_LIMIT_VIOLATION")
    if any_output_limit:
        factors.append("OUTPUT_LIMIT_VIOLATION")
    if sequence_risk == RiskLevel.HIGH:
        factors.append("SEQUENCE_ALERT_HIGH")
    elif sequence_risk == RiskLevel.MEDIUM:
        factors.append("SEQUENCE_ALERT_MEDIUM")
    if final_status == "ACCEPTED" and not factors:
        factors.append("CLEAN_ACCEPTED")
    return factors


# ---------------------------------------------------------------------------
# Persistence layer (requires a configured database)
# ---------------------------------------------------------------------------
@dataclass
class TrustSnapshot:
    workspace_label: str
    score: int
    monitoring_level: str
    open_alerts: int
    history_entries: int


def current_score(session: Session, workspace_label: str) -> int:
    row = session.execute(
        select(TrustHistory)
        .where(TrustHistory.workspace_label == workspace_label)
        .order_by(TrustHistory.created_at.desc(), TrustHistory.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return int(row.new_score) if row else INITIAL_TRUST_SCORE


def open_alert_count(session: Session, workspace_label: str) -> int:
    """Number of PENDING (unreviewed) alerts across the label's submissions."""
    from sqlalchemy import func

    from app.models.security_alert import SecurityAlert
    from app.models.submission import Submission

    count_stmt = (
        select(func.count(SecurityAlert.id))
        .join(Submission, Submission.id == SecurityAlert.submission_id)
        .where(
            Submission.workspace_label == workspace_label,
            SecurityAlert.review_status == "PENDING",
        )
    )
    return int(session.execute(count_stmt).scalar_one())


def snapshot(session: Session, workspace_label: str) -> TrustSnapshot:
    score = current_score(session, workspace_label)
    alerts = open_alert_count(session, workspace_label)
    from sqlalchemy import func

    entries = int(
        session.execute(
            select(func.count(TrustHistory.id)).where(
                TrustHistory.workspace_label == workspace_label
            )
        ).scalar_one()
    )
    return TrustSnapshot(
        workspace_label=workspace_label,
        score=score,
        monitoring_level=monitoring_level(score, alerts),
        open_alerts=alerts,
        history_entries=entries,
    )


def record_factors(
    session: Session,
    workspace_label: str,
    factors: Sequence[str],
    related_submission_id: int | None,
) -> list[TrustHistory]:
    """Apply factors for one submission; deduplicated per (label, submission, factor).

    Existing rows for the same combination are skipped, so re-analysis of the
    same submission never double-penalises. The DB unique constraint is the
    final backstop.
    """
    recorded: list[TrustHistory] = []
    score = current_score(session, workspace_label)
    for code in factors:
        factor = TRUST_FACTORS.get(code)
        if factor is None or factor.delta == 0:
            continue  # zero-delta factors are documented, never recorded
        exists = session.execute(
            select(TrustHistory.id).where(
                TrustHistory.workspace_label == workspace_label,
                TrustHistory.related_submission_id == related_submission_id,
                TrustHistory.reason == code,
            )
        ).first()
        if exists:
            continue
        new_score = clamp_score(score + factor.delta)
        row = TrustHistory(
            workspace_label=workspace_label,
            previous_score=score,
            new_score=new_score,
            reason=code,
            related_submission_id=related_submission_id,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            logger.info("Duplicate trust update skipped (%s / %s).", workspace_label, code)
            continue
        recorded.append(row)
        score = new_score
    return recorded


def record_faculty_decision(
    session: Session,
    workspace_label: str,
    decision: str,
    related_submission_id: int | None,
) -> list[TrustHistory]:
    """Record a CONFIRMED (penalty) or DISMISSED (restoration) faculty decision.

    A decision is recorded at most once per (label, submission, decision);
    faculty decisions bypass the judge's per-factor dedupe but are still
    idempotent, so repeated PATCH calls do not double-penalise or double-restore.
    """
    factor_code = {
        "CONFIRMED": "FACULTY_CONFIRMED_FINDING",
        "DISMISSED": "FACULTY_DISMISSED_FINDING",
    }.get(decision)
    if factor_code is None:
        return []
    factor = TRUST_FACTORS[factor_code]
    from sqlalchemy import func

    existing = session.execute(
        select(func.count(TrustHistory.id)).where(
            TrustHistory.workspace_label == workspace_label,
            TrustHistory.related_submission_id == related_submission_id,
            TrustHistory.reason == factor_code,
        )
    ).scalar_one()
    if existing:
        return []
    score = current_score(session, workspace_label)
    row = TrustHistory(
        workspace_label=workspace_label,
        previous_score=score,
        new_score=clamp_score(score + factor.delta),
        reason=factor_code,
        related_submission_id=related_submission_id,
    )
    session.add(row)
    session.flush()
    return [row]
