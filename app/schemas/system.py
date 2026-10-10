"""System status schemas — truthful capability reporting.

A capability is reported ACTIVE only when the mechanism is actually in use
and verified; modules that merely exist are NOT reported as active.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DatabaseStatus(BaseModel):
    configured: bool
    config_state: str
    connectivity: str
    reason: str
    display_url: str | None = Field(default=None, description="Credential-masked URL; password always ****.")


class CompilerStatus(BaseModel):
    language: str
    compiler: str
    available: bool
    path: str | None = Field(default=None, description="Executable path, or null when unavailable.")
    version: str | None = Field(default=None, description="First line of `compiler --version`, or null.")
    detail: str = ""


class JobObjectStatus(BaseModel):
    available: bool
    detail: str = Field(description="Why unavailable, or how it was verified.")


class WorkerStatus(BaseModel):
    running: bool
    active_executions: int
    queued_jobs: int
    max_concurrent_executions: int
    max_queued_jobs: int


class CapabilityInfo(BaseModel):
    id: str
    name: str
    status: Literal["ACTIVE", "MEASURED_ONLY", "PARTIAL", "NOT_AVAILABLE", "RESERVED"]
    detail: str


class SystemStatusResponse(BaseModel):
    app: str
    version: str
    environment: str
    os: dict[str, str]
    python: dict[str, str]
    auth_mode: str = Field(description="local_dev = NO authentication enforced; localhost only.")
    running_as_admin: bool = Field(description="True when the backend process is elevated (not recommended).")
    database: DatabaseStatus
    compilers: list[CompilerStatus]
    supported_languages: list[str]
    job_object: JobObjectStatus
    execution_worker: WorkerStatus
    capabilities: list[CapabilityInfo]
    warnings: list[str] = Field(default_factory=list)
