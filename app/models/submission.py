"""Submission model.

``workspace_label`` is a local, client-supplied label used only to group
trust history on this machine. It is NOT a verified student identity.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base, utcnow

if TYPE_CHECKING:
    from app.models.behavior_event import BehaviorEvent
    from app.models.behavior_fingerprint import BehaviorFingerprint
    from app.models.security_alert import SecurityAlert
    from app.models.test_result import TestResult


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        Index("ix_submissions_workspace_label_created_at", "workspace_label", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    problem_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("problems.id", ondelete="SET NULL"), nullable=True, index=True
    )
    workspace_label: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    source_code: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED", index=True)
    compile_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpu_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    peak_memory_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: True when this submission is a custom-input run (POST /submissions/run).
    is_custom_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: stdin for custom-input runs only (NULL for problem submissions).
    custom_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Snapshot of judge-time details: compilation info, execution policy,
    #: per-limit enforcement map, ACL/cleanup outcomes, and the sequence /
    #: hardcoding analysis results produced while judging this submission.
    judge_details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    test_results: Mapped[list["TestResult"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan", order_by="TestResult.test_index"
    )
    fingerprint: Mapped["BehaviorFingerprint | None"] = relationship(
        back_populates="submission", cascade="all, delete-orphan", uselist=False
    )
    events: Mapped[list["BehaviorEvent"]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="BehaviorEvent.occurred_at",
    )
    alerts: Mapped[list["SecurityAlert"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )
