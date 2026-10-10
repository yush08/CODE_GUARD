"""Submission request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.constants import Language, SubmissionStatus, TestStatus


class RunRequest(BaseModel):
    """POST /submissions/run — execute with custom stdin (no test evaluation)."""

    language: Language
    source_code: str = Field(min_length=1)
    input: str = Field(default="", description="stdin fed to the program.")
    workspace_label: str | None = Field(default=None, max_length=120)


class SubmitRequest(BaseModel):
    """POST /submissions — evaluate against a problem's test cases."""

    problem_id: int
    language: Language
    source_code: str = Field(min_length=1)
    workspace_label: str | None = Field(default=None, max_length=120)


class TestResultPublic(BaseModel):
    """Safe per-test result. Hidden test inputs/expected outputs are never here."""

    id: int
    test_index: int
    test_is_public: bool
    test_is_generated: bool
    status: TestStatus
    execution_time_ms: int | None
    memory_bytes: int | None
    exit_code: int | None
    error_summary: str | None
    output_summary: str | None = Field(
        default=None, description="Truncated normalized output produced by the program."
    )


class SubmissionSummary(BaseModel):
    id: int
    problem_id: int | None
    language: str
    status: SubmissionStatus
    workspace_label: str | None
    is_custom_run: bool
    execution_time_ms: int | None
    cpu_time_ms: int | None
    peak_memory_bytes: int | None
    exit_code: int | None
    error_summary: str | None
    created_at: datetime
    completed_at: datetime | None


class SubmissionDetail(SubmissionSummary):
    source_code: str
    compile_output: str | None
    test_results: list[TestResultPublic] = Field(default_factory=list)


class SubmissionAccepted(BaseModel):
    id: int
    status: str
    detail_url: str
    message: str = "Submission queued for execution."
