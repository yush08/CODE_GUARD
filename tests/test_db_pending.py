"""Database-backed tests.

MARKED ``db`` ON PURPOSE: they run only when ``DATABASE_URL`` in ``.env`` holds
real PostgreSQL credentials. Until the password is provided they are skipped
with an explicit "pending credentials" reason — they are never silently
rerouted to SQLite or an in-memory database.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


def test_database_is_reachable() -> None:
    from sqlalchemy import text

    from app.db.session import session_scope

    with session_scope() as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1


def test_schema_exists_after_migration() -> None:
    from sqlalchemy import inspect

    from app.db.session import get_engine

    tables = set(inspect(get_engine()).get_table_names())
    from app.db.base import Base

    missing = set(Base.metadata.tables) - tables
    assert not missing, (
        f"Missing tables: {missing} — run: .\\.venv\\Scripts\\python.exe -m alembic upgrade head"
    )


def test_submission_round_trip_with_analysis() -> None:
    from app.db.session import session_scope
    from app.models.behavior_event import BehaviorEvent
    from app.models.problem import Problem
    from app.models.submission import Submission
    from app.repositories import problem_repository as repo
    from app.services.test_generator import generate_cases

    with session_scope() as session:
        problem = repo.create_problem(
            session,
            {
                "title": "DB roundtrip problem",
                "slug": "db-roundtrip-problem",
                "description": "test",
                "input_format": "",
                "output_format": "",
                "constraints": "",
                "difficulty": "EASY",
                "supported_languages": ["c", "cpp"],
                "visibility": "PUBLIC",
            },
        )
        for case in generate_cases("a-plus-b", count=3, public=1):
            repo.add_test_case(
                session,
                problem,
                {
                    "input_data": case.input_data,
                    "expected_output": case.expected_output,
                    "is_public": case.is_public,
                    "is_generated": False,
                    "label": case.label,
                },
            )
        submission = Submission(
            problem_id=problem.id,
            language="c",
            source_code="int main(void){return 0;}",
            workspace_label="db-test",
            status="QUEUED",
            is_custom_run=False,
        )
        session.add(submission)
        session.flush()
        submission.status = "ACCEPTED"
        submission.judge_details_json = {"policy": {"timeout_ms": 1000}}
        session.add(
            BehaviorEvent(
                submission_id=submission.id,
                event_type="PROCESS_START",
                source="OBSERVED",
                severity="INFO",
                details_json={},
            )
        )
        session.flush()
        sid, pid = submission.id, problem.id

    # cleanup keeps the shared database tidy for the next run
    with session_scope() as session:
        from app.models.test_result import TestResult  # noqa: F401

        problem = session.get(Problem, pid)
        if problem is not None:
            session.delete(problem)
        submission = session.get(Submission, sid)
        if submission is not None:
            session.delete(submission)

    assert sid > 0 and pid > 0


def test_trust_history_deduplicates() -> None:
    from app.db.session import session_scope
    from app.models.submission import Submission
    from app.services import trust_service

    label = "db-trust-test"
    with session_scope() as session:
        submission = Submission(
            problem_id=None,
            language="c",
            source_code="int main(void){return 0;}",
            workspace_label=label,
            status="ACCEPTED",
            is_custom_run=True,
        )
        session.add(submission)
        session.flush()
        sid = submission.id

    try:
        with session_scope() as session:
            first = trust_service.record_factors(
                session, label, ["PROCESS_LIMIT_VIOLATION"], sid
            )
            second = trust_service.record_factors(
                session, label, ["PROCESS_LIMIT_VIOLATION"], sid
            )
            assert len(first) == 1
            assert second == [], "the same factor must never be recorded twice"
            snap = trust_service.snapshot(session, label)
            assert snap.score == 80 - 15
    finally:
        with session_scope() as session:
            submission = session.get(Submission, sid)
            if submission is not None:
                session.delete(submission)
            from sqlalchemy import delete

            from app.models.trust_history import TrustHistory

            session.execute(delete(TrustHistory).where(TrustHistory.workspace_label == label))
