"""Problem & test-case endpoints.

Student-facing responses (list/detail) only ever include PUBLIC sample tests.
Hidden and generated test cases are visible exclusively through the explicit
``/problems/{id}/test-cases`` management endpoint, which is documented as
faculty/admin-facing — role enforcement is not implemented yet (local-dev
mode; see README "Implemented / Partial / Unimplemented").
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, pagination_params
from app.core.config import get_settings
from app.core.constants import Visibility
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.models.problem import Problem
from app.models.test_case import TestCase
from app.repositories import problem_repository as repo
from app.schemas.common import Page, build_page
from app.schemas.problem import (
    ProblemCreate,
    ProblemDetail,
    ProblemSummary,
    ProblemUpdate,
    TestCaseAdmin,
    TestCaseCounts,
    TestCaseCreate,
    TestCasePublic,
)

router = APIRouter(prefix="/problems", tags=["problems"])


def _counts(problem_id: int, session: Session) -> TestCaseCounts:
    return TestCaseCounts(**repo.count_test_cases(session, problem_id))


def _summary(problem: Problem, session: Session) -> ProblemSummary:
    return ProblemSummary(
        id=problem.id,
        title=problem.title,
        slug=problem.slug,
        difficulty=problem.difficulty,
        visibility=problem.visibility,
        time_limit_ms=problem.time_limit_ms,
        memory_limit_mb=problem.memory_limit_mb,
        process_limit=problem.process_limit,
        test_case_counts=_counts(problem.id, session),
        created_at=problem.created_at,
    )


def _validate_limits(payload: dict) -> None:
    settings = get_settings()
    checks = (
        ("time_limit_ms", payload.get("time_limit_ms"), settings.max_allowed_time_limit_ms, "ms"),
        ("memory_limit_mb", payload.get("memory_limit_mb"), settings.max_allowed_memory_limit_mb, "MB"),
        ("process_limit", payload.get("process_limit"), settings.max_allowed_process_limit, "processes"),
    )
    for name, value, ceiling, unit in checks:
        if value is not None and value > ceiling:
            raise ValidationFailedError(
                f"{name}={value} exceeds the server maximum of {ceiling} {unit}.",
                details={"field": name, "maximum": ceiling},
            )


def _detail(problem: Problem, session: Session) -> ProblemDetail:
    base = _summary(problem, session)
    public_cases = list(
        session.scalars(
            select(TestCase)
            .where(TestCase.problem_id == problem.id, TestCase.is_public.is_(True))
            .order_by(TestCase.id)
        )
    )
    return ProblemDetail(
        **base.model_dump(),
        description=problem.description,
        input_format=problem.input_format,
        output_format=problem.output_format,
        constraints=problem.constraints,
        supported_languages=list(problem.supported_languages or []),
        sample_tests=[
            TestCasePublic(
                id=c.id,
                input_data=c.input_data,
                expected_output=c.expected_output,
                label=c.label,
            )
            for c in public_cases
        ],
        updated_at=problem.updated_at,
    )


@router.get("", response_model=Page[ProblemSummary], summary="List problems (public samples only)")
def list_problems(
    session: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    difficulty: str | None = Query(None),
    search: str | None = Query(None, max_length=200),
    include_all_visibilities: bool = Query(
        False,
        description="Include non-public problems (faculty/admin capability; "
        "no role enforcement exists in local-dev mode).",
    ),
) -> Page[ProblemSummary]:
    visibility = None if include_all_visibilities else Visibility.PUBLIC.value
    rows, total = repo.list_problems(
        session,
        offset=(page - 1) * page_size,
        limit=page_size,
        visibility=visibility,
        difficulty=difficulty,
        search=search,
    )
    return build_page([_summary(p, session) for p in rows], total, page, page_size)


@router.post("", response_model=ProblemDetail, status_code=status.HTTP_201_CREATED)
def create_problem(payload: ProblemCreate, session: Session = Depends(get_db)) -> ProblemDetail:
    data = payload.model_dump(exclude={"test_cases", "slug"})
    _validate_limits(data)
    data["supported_languages"] = [lang.value for lang in payload.supported_languages]
    problem = repo.create_problem(session, data)
    for case in payload.test_cases:
        repo.add_test_case(session, problem, case.model_dump())
    session.flush()
    return _detail(problem, session)


@router.get("/{problem_id}", response_model=ProblemDetail)
def get_problem(problem_id: int, session: Session = Depends(get_db)) -> ProblemDetail:
    problem = repo.get_problem(session, problem_id)
    if problem is None:
        raise NotFoundError(f"Problem {problem_id} does not exist.")
    return _detail(problem, session)


@router.patch("/{problem_id}", response_model=ProblemDetail)
def update_problem(
    problem_id: int, payload: ProblemUpdate, session: Session = Depends(get_db)
) -> ProblemDetail:
    problem = repo.get_problem(session, problem_id)
    if problem is None:
        raise NotFoundError(f"Problem {problem_id} does not exist.")
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    _validate_limits(data)
    if "supported_languages" in data:
        data["supported_languages"] = [lang.value for lang in payload.supported_languages or []]
    repo.update_problem(session, problem, data)
    session.flush()
    return _detail(problem, session)


@router.delete("/{problem_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_problem(problem_id: int, session: Session = Depends(get_db)) -> None:
    problem = repo.get_problem(session, problem_id)
    if problem is None:
        raise NotFoundError(f"Problem {problem_id} does not exist.")
    session.delete(problem)
    session.flush()


# ---------------------------------------------------------------------------
# Test-case management (faculty/admin surface — hidden data lives here only)
# ---------------------------------------------------------------------------
@router.get(
    "/{problem_id}/test-cases",
    response_model=list[TestCaseAdmin],
    summary="All test cases including hidden ones (faculty/admin surface)",
)
def list_test_cases(problem_id: int, session: Session = Depends(get_db)) -> list[TestCaseAdmin]:
    problem = repo.get_problem(session, problem_id)
    if problem is None:
        raise NotFoundError(f"Problem {problem_id} does not exist.")
    cases = list(
        session.scalars(
            select(TestCase)
            .where(TestCase.problem_id == problem_id)
            .order_by(TestCase.is_public.desc(), TestCase.id)
        )
    )
    return [
        TestCaseAdmin(
            id=c.id,
            problem_id=c.problem_id,
            input_data=c.input_data,
            expected_output=c.expected_output,
            is_public=c.is_public,
            is_generated=c.is_generated,
            label=c.label,
            created_at=c.created_at,
        )
        for c in cases
    ]


@router.post(
    "/{problem_id}/test-cases",
    response_model=TestCaseAdmin,
    status_code=status.HTTP_201_CREATED,
)
def add_test_case(
    problem_id: int, payload: TestCaseCreate, session: Session = Depends(get_db)
) -> TestCaseAdmin:
    problem = repo.get_problem(session, problem_id)
    if problem is None:
        raise NotFoundError(f"Problem {problem_id} does not exist.")
    settings = get_settings()
    total = sum(repo.count_test_cases(session, problem_id).values())
    if total + 1 > settings.max_allowed_test_cases:
        raise ValidationFailedError(
            f"A problem may have at most {settings.max_allowed_test_cases} test cases."
        )
    case = repo.add_test_case(session, problem, payload.model_dump())
    session.flush()
    return TestCaseAdmin(
        id=case.id,
        problem_id=case.problem_id,
        input_data=case.input_data,
        expected_output=case.expected_output,
        is_public=case.is_public,
        is_generated=case.is_generated,
        label=case.label,
        created_at=case.created_at,
    )


@router.delete("/{problem_id}/test-cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_test_case(problem_id: int, case_id: int, session: Session = Depends(get_db)) -> None:
    case = session.get(TestCase, case_id)
    if case is None or case.problem_id != problem_id:
        raise NotFoundError(f"Test case {case_id} does not exist for problem {problem_id}.")
    session.delete(case)
    session.flush()
