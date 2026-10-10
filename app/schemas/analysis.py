"""Analysis endpoint schemas: fingerprint, events, sequence, hardcoding, report."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.submission import SubmissionSummary, TestResultPublic


class FingerprintResponse(BaseModel):
    submission_id: int | str | None
    analysis_type: str
    measurement_source: str = Field(description="OBSERVED for real executions.")
    fingerprint: dict[str, Any]
    measurements: dict[str, str] = Field(
        description="Per-metric availability: MEASURED or UNAVAILABLE (nulls are never invented)."
    )
    notes: list[str] = Field(default_factory=list)


class EventResponse(BaseModel):
    id: int
    event_type: str
    occurred_at: datetime
    source: str = Field(description="OBSERVED | INFERRED | SIMULATED")
    severity: str
    details: dict[str, Any] = Field(default_factory=dict)


class SequenceMatch(BaseModel):
    rule_id: str
    risk_level: str
    explanation: str
    creates_alert: bool
    matched_at_index: int
    matched_event_types: list[str]
    evidence: list[dict[str, Any]]


class SequenceAnalysisResponse(BaseModel):
    analysis_type: str
    risk_level: str
    rule: str | None
    observed_events: list[str]
    explanation: str
    source: str
    rules_evaluated: int
    matches: list[SequenceMatch] = Field(default_factory=list)


class HardcodingAnalysisResponse(BaseModel):
    analysis_type: str
    status: str = Field(description="NO_SIGNAL | REVIEW_RECOMMENDED | INSUFFICIENT_DATA")
    evidence: list[str] = Field(default_factory=list)
    confidence: None = Field(
        default=None, description="Always null: no validated confidence scoring exists."
    )
    explanation: str
    supporting_tests: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Combined report
# ---------------------------------------------------------------------------
class ReportCompilation(BaseModel):
    attempted: bool
    success: bool | None = None
    compiler: str | None = None
    compiler_version: str | None = None
    compiler_path: str | None = None
    flags: str | None = Field(default=None, description="Compiler flags actually used.")
    duration_ms: int | None = None
    output: str | None = Field(default=None, description="Compiler output (errors/warnings).")
    failure_reason: str | None = None


class ReportTestSummary(BaseModel):
    total: int
    accepted: int
    wrong_answer: int
    runtime_error: int
    time_limit_exceeded: int
    memory_limit_exceeded: int
    process_limit_exceeded: int
    results: list[TestResultPublic] = Field(default_factory=list)


class ReportAnalysis(BaseModel):
    fingerprint: dict[str, Any] | None = None
    sequence_analysis: dict[str, Any] | None = None
    hardcoding_analysis: dict[str, Any] | None = None


class ReportAlertItem(BaseModel):
    id: int
    alert_type: str
    severity: str
    review_status: str
    explanation: str
    created_at: datetime


class ReportTrust(BaseModel):
    workspace_label: str | None
    current_score: int | None = None
    monitoring_level: str | None = None
    open_alerts: int | None = None
    note: str = (
        "Workspace-scoped history for this machine only; a workspace label is "
        "not a verified student identity."
    )


class ReportResponse(BaseModel):
    generated_at: datetime
    submission: SubmissionSummary
    problem: dict[str, Any] | None = Field(
        default=None, description="id/title/slug/limits — never hidden test data."
    )
    compilation: ReportCompilation
    test_summary: ReportTestSummary
    resource_measurements: dict[str, Any] = Field(
        default_factory=dict,
        description="Enforcement map + raw measurements (null = not measured).",
    )
    analysis: ReportAnalysis
    alerts: list[ReportAlertItem] = Field(default_factory=list)
    trust: ReportTrust | None = None
    limitations: list[str] = Field(default_factory=list)
