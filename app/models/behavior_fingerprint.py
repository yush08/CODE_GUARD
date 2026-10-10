"""BehaviorFingerprint model — the derived summary of one execution.

The fingerprint only contains values the engine actually measured; missing
measurements are stored as JSON ``null``. ``measurement_source`` records the
provenance (OBSERVED for real executions).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base, utcnow

if TYPE_CHECKING:
    from app.models.submission import Submission


class BehaviorFingerprint(Base):
    __tablename__ = "behavior_fingerprints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    fingerprint_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    measurement_source: Mapped[str] = mapped_column(String(16), nullable=False, default="OBSERVED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )

    submission: Mapped["Submission"] = relationship(back_populates="fingerprint")
