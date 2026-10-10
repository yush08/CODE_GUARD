"""GET /api/v1/health — application liveness + database health.

The application can be fully alive while the database is unconfigured; this
endpoint reports both facts separately and never pretends storage works.
"""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.core.config import get_settings
from app.db.session import probe_database
from app.schemas.health import DatabaseHealth, HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, summary="Application + database health")
def health() -> HealthResponse:
    settings = get_settings()
    probe = probe_database(settings)
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=__version__,
        database=DatabaseHealth(**probe.as_dict()),
    )
