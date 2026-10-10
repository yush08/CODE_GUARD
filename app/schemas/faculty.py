"""Faculty dashboard / alert / trust schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.constants import ReviewStatus
from app.schemas.submission import SubmissionSummary


class AlertSummary(BaseModel):
    id: int
    submission_id: int
    alert_type: str
    severity: str
    review_status: ReviewStatus
    explanation: str
    created_at: datetime


class AlertDetail(AlertSummary):
    evidence_json: dict[str, Any] = Field(default_factory=dict)
    reviewed_by: str | None = None
    review_note: str | None = None
    reviewed_at: datetime | None = None


class AlertReviewRequest(BaseModel):
    review_status: ReviewStatus = Field(
        description="CONFIRMED / DISMISSED / UNDER_REVIEW (PENDING reopens)."
    )
    review_note: str | None = Field(default=None, max_length=2000)
    reviewed_by: str | None = Field(
        default=None,
        max_length=120,
        description="Reviewer identifier (defaults to the caller's workspace label).",
    )


class DashboardResponse(BaseModel):
    generated_at: datetime
    submissions: dict[str, Any]
    alerts: dict[str, Any]
    resource_metric_coverage: dict[str, Any]
    recent_submissions: list[SubmissionSummary] = Field(default_factory=list)
    recent_alerts: list[AlertSummary] = Field(default_factory=list)
    note: str = "All values are aggregates of stored records; empty database = real zeros."


class TrustHistoryEntry(BaseModel):
    id: int
    workspace_label: str
    previous_score: int
    new_score: int
    reason: str
    related_submission_id: int | None
    created_at: datetime


class TrustHistoryResponse(BaseModel):
    workspace_label: str
    current_score: int
    monitoring_level: str
    open_alerts: int
    history_entries: int
    formula: dict[str, Any]
    entries: list[TrustHistoryEntry] = Field(default_factory=list)
    note: str = (
        "Workspace-scoped monitoring history on this machine only. A local "
        "workspace label is not a verified student identity, and the score "
        "never weakens resource or security controls."
    )
