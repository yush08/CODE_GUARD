"""TrustHistory model — auditable record of monitoring-score changes.

Scoping note: history is keyed by ``workspace_label`` (a local label) or an
authenticated-user reference once real authentication exists. A local
workspace label is NOT a verified student identity.

The unique constraint on (workspace_label, related_submission_id, reason)
prevents duplicate updates for the same submission/factor combination.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow

if TYPE_CHECKING:
    from app.models.submission import Submission


class TrustHistory(Base):
    __tablename__ = "trust_history"
    __table_args__ = (
        UniqueConstraint(
            "workspace_label",
            "related_submission_id",
            "reason",
            name="uq_trust_history_label_submission_reason",
        ),
        CheckConstraint("new_score >= 0 AND new_score <= 100", name="ck_trust_score_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_label: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    previous_score: Mapped[int] = mapped_column(Integer, nullable=False)
    new_score: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Stable factor code from app.core.constants.TRUST_FACTORS.
    reason: Mapped[str] = mapped_column(String(60), nullable=False)
    related_submission_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("submissions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )

    submission: Mapped["Submission | None"] = relationship()
