"""Shared FastAPI dependencies: database session, pagination, actor."""

from __future__ import annotations

from typing import Iterator

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Actor, get_actor
from app.db.session import get_session_factory, require_database
from app.schemas.common import PaginationParams


def get_db() -> Iterator[Session]:
    """Yield a request-scoped session.

    Raises a 503 DATABASE_NOT_CONFIGURED / DATABASE_UNAVAILABLE error before
    any query when the database is not usable — endpoints never return
    fabricated data in database-unconfigured mode.
    """
    require_database()
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def pagination_params(
    page: int = Query(1, ge=1, description="1-based page number."),
    page_size: int = Query(20, ge=1, le=100, description="Items per page (1-100)."),
) -> PaginationParams:
    return PaginationParams(page=page, page_size=page_size)


DbSession = Depends(get_db)
CurrentActor = Depends(get_actor)
