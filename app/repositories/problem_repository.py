"""Problem and test-case persistence."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy import func, select, or_
from sqlalchemy.orm import Session

from app.models.problem import Problem
from app.models.test_case import TestCase


def slugify(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text or "problem")[:200]


def _unique_slug(session: Session, base: str) -> str:
    slug = base
    n = 2
    while session.execute(select(Problem.id).where(Problem.slug == slug)).first():
        slug = f"{base}-{n}"
        n += 1
    return slug


def list_problems(
    session: Session,
    *,
    offset: int,
    limit: int,
    visibility: str | None = None,
    difficulty: str | None = None,
    search: str | None = None,
) -> tuple[list[Problem], int]:
    stmt = select(Problem)
    if visibility:
        stmt = stmt.where(Problem.visibility == visibility)
    if difficulty:
        stmt = stmt.where(Problem.difficulty == difficulty)
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(or_(Problem.title.ilike(pattern), Problem.slug.ilike(pattern)))
    total = int(
        session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    )
    rows = list(
        session.execute(stmt.order_by(Problem.id).offset(offset).limit(limit)).scalars()
    )
    return rows, total


def get_problem(session: Session, problem_id: int) -> Problem | None:
    return session.get(Problem, problem_id)


def count_test_cases(session: Session, problem_id: int) -> dict[str, int]:
    rows = session.execute(
        select(TestCase.is_public, TestCase.is_generated, func.count())
        .where(TestCase.problem_id == problem_id)
        .group_by(TestCase.is_public, TestCase.is_generated)
    ).all()
    counts = {"public": 0, "hidden": 0, "generated": 0}
    for is_public, is_generated, count in rows:
        if is_public:
            counts["public"] += count
        else:
            counts["hidden"] += count
        if is_generated:
            counts["generated"] += count
    return counts


def create_problem(session: Session, data: dict[str, Any]) -> Problem:
    payload = dict(data)
    if not payload.get("slug"):
        payload["slug"] = _unique_slug(session, slugify(payload["title"]))
    problem = Problem(**payload)
    session.add(problem)
    session.flush()
    return problem


def update_problem(session: Session, problem: Problem, data: dict[str, Any]) -> Problem:
    for key, value in data.items():
        if value is not None:
            setattr(problem, key, value)
    session.flush()
    return problem


def add_test_case(session: Session, problem: Problem, data: dict[str, Any]) -> TestCase:
    case = TestCase(problem_id=problem.id, **data)
    session.add(case)
    session.flush()
    return case


def judge_test_cases(session: Session, problem: Problem) -> list[TestCase]:
    """All test cases in stable order — server-side only, never serialized."""
    return list(
        session.execute(
            select(TestCase)
            .where(TestCase.problem_id == problem.id)
            .order_by(TestCase.is_public.desc(), TestCase.id)
        ).scalars()
    )
