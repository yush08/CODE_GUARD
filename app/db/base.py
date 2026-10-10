"""SQLAlchemy 2.x declarative base and shared column helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Timezone-aware UTC timestamp (Python side default)."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for all CodeGuard models."""


Timestamps = {
    "created_at": mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    ),
}


def created_at_column() -> Mapped[datetime]:
    return mapped_column(  # type: ignore[return-value]
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
