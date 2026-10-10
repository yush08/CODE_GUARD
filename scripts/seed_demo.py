r"""Seed reproducible demo data (problems + optional demo submissions).

Requires a configured, reachable PostgreSQL database — there is no SQLite
fallback, so this script reports a clear message and exits instead of
inventing data when credentials are missing.

PowerShell:
    .\.venv\Scripts\python.exe -m alembic upgrade head   # once, creates tables
    .\.venv\Scripts\python.exe scripts\seed_demo.py                     # problems only
    .\.venv\Scripts\python.exe scripts\seed_demo.py --run-submissions   # + demo runs

Exit codes: 0 success, 2 database not configured/reachable, 3 schema missing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.session import probe_database, session_scope  # noqa: E402
from app.repositories import problem_repository as repo  # noqa: E402
from app.services.test_generator import generate_cases  # noqa: E402

DEMO_WORKSPACE = "demo-workspace"

DEMO_PROBLEMS = [
    {
        "title": "A + B",
        "slug": "a-plus-b",
        "description": (
            "Read two integers separated by a space and print their sum.\n\n"
            "This is the demo problem used in the CodeGuard walkthrough."
        ),
        "input_format": "One line: two integers a and b (|a|, |b| <= 10^6).",
        "output_format": "One line: a + b.",
        "constraints": "-10^6 <= a, b <= 10^6",
        "difficulty": "EASY",
        "time_limit_ms": 1000,
        "memory_limit_mb": 64,
        "process_limit": 4,
        "cases": {"count": 6, "public": 2},
    },
    {
        "title": "Max of N numbers",
        "slug": "max-of-n-numbers",
        "description": "Read n, then n integers. Print the maximum value.",
        "input_format": "Line 1: n. Line 2: n integers.",
        "output_format": "One line: the maximum value.",
        "constraints": "1 <= n <= 50",
        "difficulty": "EASY",
        "time_limit_ms": 1000,
        "memory_limit_mb": 64,
        "process_limit": 4,
        "cases": {"count": 5, "public": 1},
    },
    {
        "title": "Count odd numbers",
        "slug": "count-odd-numbers",
        "description": "Read n, then n integers. Print how many are odd.",
        "input_format": "Line 1: n. Line 2: n integers.",
        "output_format": "One line: the count of odd values.",
        "constraints": "1 <= n <= 60",
        "difficulty": "EASY",
        "time_limit_ms": 1000,
        "memory_limit_mb": 64,
        "process_limit": 4,
        "cases": {"count": 5, "public": 1},
    },
]

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "programs"

#: (fixture file, expected submission status, note). The middle column is
#: descriptive only — run_demo_submissions prints the real status read back
#: from the database. For hardcoded_sum.c the highlight is the hardcoding
#: status (REVIEW_RECOMMENDED), not the judge status.
DEMO_SUBMISSIONS = [
    ("sum_correct.c", "ACCEPTED", "correct C solution"),
    ("sum_correct.cpp", "ACCEPTED", "correct C++ solution"),
    ("sum_wrong.c", "WRONG_ANSWER", "wrong answer (no trust penalty)"),
    ("hardcoded_sum.c", "REVIEW_RECOMMENDED", "hardcoding heuristic fires"),
    ("infinite_loop.c", "TIME_LIMIT_EXCEEDED", "wall-clock limit enforced"),
    ("process_spawner.c", "PROCESS_LIMIT_EXCEEDED", "process limit enforced"),
]


def seed_problems() -> list[str]:
    created: list[str] = []
    with session_scope() as session:
        for spec in DEMO_PROBLEMS:
            existing = session.execute(
                select_problem_by_slug(spec["slug"])
            ).scalar_one_or_none()
            if existing is not None:
                print(f"  = exists : {spec['title']} (/{spec['slug']})")
                continue
            payload = {
                k: v for k, v in spec.items() if k not in {"slug", "cases"}
            }
            problem = repo.create_problem(session, {**payload, "slug": spec["slug"]})
            for case in generate_cases(spec["slug"], **spec["cases"]):
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
            created.append(spec["slug"])
            print(f"  + created: {spec['title']} (/{spec['slug']})")
    return created


def select_problem_by_slug(slug: str):
    from sqlalchemy import select

    from app.models.problem import Problem

    return select(Problem.id).where(Problem.slug == slug)


def run_demo_submissions() -> None:
    from app.models.submission import Submission
    from app.repositories import submission_repository as sub_repo
    from app.workers.execution_worker import process_submission

    with session_scope() as session:
        problem = session.execute(
            select_problem_by_slug("a-plus-b")
        ).scalar_one_or_none()
        if problem is None:
            print("  ! demo problem 'a-plus-b' missing; run without --run-submissions first")
            return
        problem_id = int(problem)

    for fixture, _expected, note in DEMO_SUBMISSIONS:
        source = (FIXTURES / fixture).read_text(encoding="utf-8", errors="replace")
        with session_scope() as session:
            sub = sub_repo.create_submission(
                session,
                problem_id=problem_id,
                language="cpp" if fixture.endswith(".cpp") else "c",
                source_code=source,
                workspace_label=DEMO_WORKSPACE,
                is_custom_run=False,
            )
            submission_id = sub.id
        process_submission(submission_id)  # synchronous: no queue needed here
        with session_scope() as session:
            row = session.get(Submission, submission_id)
            print(
                f"  > {fixture:20} -> {row.status:<24} ({note})"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-submissions",
        action="store_true",
        help="Also judge the demo fixture programs against 'A + B'.",
    )
    args = parser.parse_args()

    settings = get_settings()
    probe = probe_database(settings, force=True)
    print(f"database: {probe.config_state.value} / {probe.connectivity.value}")
    if not probe.is_usable:
        print(f"  ! {probe.reason}")
        print("  ! No SQLite/in-memory fallback exists - configure .env first.")
        return 2

    try:
        with session_scope() as session:
            session.execute(text("SELECT 1 FROM problems LIMIT 1"))
    except Exception as exc:
        print(f"  ! Schema missing ({exc.__class__.__name__}).")
        print("  ! Run: .\\.venv\\Scripts\\python.exe -m alembic upgrade head")
        return 3

    print("seeding problems:")
    seed_problems()
    if args.run_submissions:
        print("judging demo submissions (this really compiles and runs code):")
        run_demo_submissions()
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
