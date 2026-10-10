"""Problem and test-case schemas.

Student-facing shapes never include hidden test inputs or expected outputs;
``ProblemDetail.sample_tests`` contains only PUBLIC test cases.
"""

from __future__ import annotations

from datetime import datetime

from app.core.constants import Difficulty, Language, Visibility
from pydantic import BaseModel, Field


class TestCaseCreate(BaseModel):
    input_data: str = ""
    expected_output: str = ""
    is_public: bool = False
    is_generated: bool = False
    label: str | None = Field(default=None, max_length=80)


class TestCaseAdmin(TestCaseCreate):
    id: int
    problem_id: int
    created_at: datetime


class TestCasePublic(BaseModel):
    """A PUBLIC example test (safe to show students)."""

    id: int
    input_data: str
    expected_output: str
    label: str | None = None


class TestCaseCounts(BaseModel):
    public: int = 0
    hidden: int = 0
    generated: int = 0


class ProblemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, max_length=220)
    description: str = ""
    input_format: str = ""
    output_format: str = ""
    constraints: str = ""
    difficulty: Difficulty = Difficulty.EASY
    supported_languages: list[Language] = Field(default_factory=lambda: [Language.C, Language.CPP])
    time_limit_ms: int | None = Field(default=None, ge=50)
    memory_limit_mb: int | None = Field(default=None, ge=8)
    process_limit: int | None = Field(default=None, ge=1)
    visibility: Visibility = Visibility.PUBLIC
    test_cases: list[TestCaseCreate] = Field(default_factory=list)


class ProblemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    input_format: str | None = None
    output_format: str | None = None
    constraints: str | None = None
    difficulty: Difficulty | None = None
    supported_languages: list[Language] | None = None
    time_limit_ms: int | None = Field(default=None, ge=50)
    memory_limit_mb: int | None = Field(default=None, ge=8)
    process_limit: int | None = Field(default=None, ge=1)
    visibility: Visibility | None = None


class ProblemSummary(BaseModel):
    id: int
    title: str
    slug: str
    difficulty: Difficulty
    visibility: Visibility
    time_limit_ms: int | None
    memory_limit_mb: int | None
    process_limit: int | None
    test_case_counts: TestCaseCounts
    created_at: datetime


class ProblemDetail(ProblemSummary):
    description: str
    input_format: str
    output_format: str
    constraints: str
    supported_languages: list[str]
    sample_tests: list[TestCasePublic] = Field(
        default_factory=list, description="PUBLIC examples only; hidden tests are never included."
    )
    updated_at: datetime
