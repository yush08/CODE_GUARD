"""BehaviorEvent model — chronological runtime events for one submission.

``source`` separates what the engine actually saw (OBSERVED) from what a rule
derived (INFERRED). SIMULATED exists only for demo fixtures and is never used
for real executions.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base, utcnow

if TYPE_CHECKING:
    from app.models.submission import Submission


class BehaviorEvent(Base):
    __tablename__ = "behavior_events"
    __table_args__ = (
        Index("ix_behavior_events_submission_occurred", "submission_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="OBSERVED")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    submission: Mapped["Submission"] = relationship(back_populates="events")
