"""TestResult model. ``test_case_id`` is NULL for custom-input runs.

``output_summary`` is a truncated, normalized copy of the program's own
output (safe to show). Hidden test inputs and expected outputs are never
stored here and never exposed to students.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow

if TYPE_CHECKING:
    from app.models.submission import Submission


class TestResult(Base):
    __tablename__ = "test_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    test_case_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: 1-based index for problem tests; 0 marks a custom-input run.
    test_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Public/hidden/generated at the time of execution (audit copy).
    test_is_public: Mapped[bool] = mapped_column(nullable=False, default=False)
    test_is_generated: Mapped[bool] = mapped_column(nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Truncated normalized actual output (safe; never the expected output).
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: SHA-256 of the normalized actual output, for hardcoding heuristics.
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )

    submission: Mapped["Submission"] = relationship(back_populates="test_results")
