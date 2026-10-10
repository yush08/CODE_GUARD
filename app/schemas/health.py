"""Health endpoint schemas.

``/api/v1/health`` distinguishes APPLICATION liveness from DATABASE health:
the app can be fully alive while the database is unconfigured or unreachable.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DatabaseHealth(BaseModel):
    configured: bool = Field(description="True only when real credentials are present.")
    config_state: str = Field(description="UNCONFIGURED_MISSING_URL | UNCONFIGURED_PLACEHOLDER | INVALID_URL | CONFIGURED")
    connectivity: str = Field(description="NOT_ATTEMPTED | REACHABLE | UNREACHABLE")
    reason: str = Field(description="Secret-free explanation of the current state.")


class HealthResponse(BaseModel):
    status: str = Field(description="'ok' — the application process is serving requests.")
    app: str
    version: str
    database: DatabaseHealth
    database_required_for_full_function: bool = True
