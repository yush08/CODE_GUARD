"""End-to-end judge pipeline tests — real compilation and execution on Windows.

These are the tests that prove the engine actually enforces what it claims.
Each test runs the DB-free pipeline with a sandbox inside pytest's tmp dir.
"""

from __future__ import annotations

import time

import pytest

from app.core.constants import Language, SubmissionStatus
from app.core.constants import TestStatus as ResultStatus
from app.core.config import Settings
from app.services.compilation_service import CompilationService
from app.services.judge_service import (
    ProblemLimits,
    judge_submission,
)
from app.services.judge_service import TestCaseSpec as Spec

pytestmark = [
    pytest.mark.windows,
    pytest.mark.skipif(
        not CompilationService().detect(Language.C).available,
        reason="No local gcc detected (set COMPILER_C_PATH in .env).",
    ),
]


def specs(*cases: tuple[str, str, bool]) -> list[Spec]:
    """(input, expected, is_public) triples -> specs."""
    return [
        Spec(
            id=i + 1,
            index=i + 1,
            input_data=inp,
            expected_output=exp,
            is_public=public,
            label=f"case-{i + 1}",
        )
        for i, (inp, exp, public) in enumerate(cases)
    ]


def assert_no_leftover_workspaces(settings: Settings) -> None:
    root = settings.sandbox_root
    leftovers = list(root.glob("sub_*")) if root.exists() else []
    assert leftovers == [], f"workspaces were not cleaned up: {leftovers}"


def test_correct_c_program_is_accepted(settings, fixture_source) -> None:
    outcome = judge_submission(
        fixture_source("sum_correct.c"),
        "c",
        specs(("2 3\n", "5\n", True), ("10 10\n", "20\n", False)),
        settings=settings,
        workspace_label="pytest",
        submission_id=101,
    )
    assert outcome.status == SubmissionStatus.ACCEPTED
    assert outcome.compile is not None and outcome.compile.success
    assert [t.status for t in outcome.tests] == [ResultStatus.ACCEPTED] * 2
    assert outcome.error_summary is None
    # measurements are real and availability is declared
    assert outcome.fingerprint["fingerprint"]["execution_time_ms"] >= 0
    assert outcome.fingerprint["measurements"]["cpu_time_ms"] in {"MEASURED", "UNAVAILABLE"}
    run = outcome.tests[0].run
    assert run.enforcement["wall_clock_deadline"] == "ENFORCED"
    assert run.enforcement["output_size_limit"] == "ENFORCED"
    # CPU-time enforcement is deliberately NEVER claimed: the kernel per-job
    # cap was verified unreliable (see execution_service.run_program).
    assert run.enforcement["cpu_time_limit"] == "MEASURED_ONLY"
    assert run.enforcement["memory_limit"] in {"ENFORCED", "MEASURED_ONLY"}
    assert_no_leftover_workspaces(settings)


def test_correct_cpp_program_is_accepted(settings, fixture_source) -> None:
    outcome = judge_submission(
        fixture_source("sum_correct.cpp"),
        "cpp",
        specs(("2 3\n", "5\n", True)),
        settings=settings,
        workspace_label="pytest",
        submission_id=102,
    )
    assert outcome.status == SubmissionStatus.ACCEPTED
    assert_no_leftover_workspaces(settings)


def test_wrong_answer_is_detected(settings, fixture_source) -> None:
    outcome = judge_submission(
        fixture_source("sum_wrong.c"),
        "c",
        specs(("2 3\n", "5\n", True)),
        settings=settings,
        workspace_label="pytest",
        submission_id=103,
    )
    assert outcome.status == SubmissionStatus.WRONG_ANSWER
    assert outcome.tests[0].status == ResultStatus.WRONG_ANSWER
    assert outcome.error_summary
    assert_no_leftover_workspaces(settings)


def test_compilation_error_is_reported_with_compiler_output(
    settings, fixture_source
) -> None:
    outcome = judge_submission(
        fixture_source("compile_error.c"),
        "c",
        specs(("2 3\n", "5\n", True)),
        settings=settings,
        workspace_label="pytest",
        submission_id=104,
    )
    assert outcome.status == SubmissionStatus.COMPILATION_ERROR
    assert outcome.compile is not None and not outcome.compile.success
    assert outcome.compile.output  # real compiler diagnostics
    assert outcome.tests == []
    assert_no_leftover_workspaces(settings)


def test_infinite_loop_hits_the_wall_clock_deadline(settings, fixture_source) -> None:
    started = time.monotonic()
    outcome = judge_submission(
        fixture_source("infinite_loop.c"),
        "c",
        specs(("0\n", "0\n", True)),
        settings=settings,
        problem_limits=ProblemLimits(time_limit_ms=700),
        workspace_label="pytest",
        submission_id=105,
    )
    elapsed = time.monotonic() - started
    assert outcome.status == SubmissionStatus.TIME_LIMIT_EXCEEDED
    run = outcome.tests[0].run
    assert run.timed_out
    assert run.exit_code == 42  # documented judge exit code for a time kill
    assert elapsed < 10, "the deadline must stop the program promptly"
    event_types = [e.event_type.value for e in outcome.monitor.events()]
    assert "TIME_LIMIT_EVENT" in event_types
    assert_no_leftover_workspaces(settings)


def test_process_limit_is_enforced_by_the_monitor(settings, fixture_source) -> None:
    outcome = judge_submission(
        fixture_source("process_spawner.c"),
        "c",
        specs(("0\n", "0\n", True)),
        settings=settings,
        problem_limits=ProblemLimits(process_limit=3),
        workspace_label="pytest",
        submission_id=106,
    )
    assert outcome.status == SubmissionStatus.PROCESS_LIMIT_EXCEEDED
    event_types = [e.event_type.value for e in outcome.monitor.events()]
    assert "PROCESS_LIMIT_EVENT" in event_types
    assert_no_leftover_workspaces(settings)


def test_memory_limit_is_enforced(settings, fixture_source) -> None:
    outcome = judge_submission(
        fixture_source("memory_hog.c"),
        "c",
        specs(("0\n", "0\n", True)),
        settings=settings,
        problem_limits=ProblemLimits(memory_limit_mb=64),
        workspace_label="pytest",
        submission_id=107,
    )
    assert outcome.status == SubmissionStatus.MEMORY_LIMIT_EXCEEDED
    assert outcome.tests[0].run.memory_limit_hit
    event_types = [e.event_type.value for e in outcome.monitor.events()]
    assert "MEMORY_LIMIT_EVENT" in event_types
    assert_no_leftover_workspaces(settings)


def test_output_storm_is_capped_and_terminated(settings, fixture_source) -> None:
    outcome = judge_submission(
        fixture_source("output_storm.c"),
        "c",
        specs(("0\n", "0\n", True)),
        settings=settings,
        workspace_label="pytest",
        submission_id=108,
    )
    assert outcome.status == SubmissionStatus.RUNTIME_ERROR
    assert "Output limit exceeded" in (outcome.error_summary or "")
    run = outcome.tests[0].run
    assert run.output_limit_hit
    assert run.enforcement["output_size_limit"] == "ENFORCED"
    # the drain threads cap capture, so a storm can never exhaust memory
    assert len(run.stdout) <= outcome.policy.max_output_bytes + 4096
    event_types = [e.event_type.value for e in outcome.monitor.events()]
    assert "OUTPUT_LIMIT_EVENT" in event_types
    assert_no_leftover_workspaces(settings)


def test_hardcoding_heuristic_recommends_review(settings, fixture_source) -> None:
    outcome = judge_submission(
        fixture_source("hardcoded_sum.c"),
        "c",
        specs(
            ("2 3\n", "5\n", True),
            ("10 10\n", "20\n", True),
            ("7 8\n", "15\n", False),
            ("1 1\n", "2\n", False),
        ),
        settings=settings,
        workspace_label="pytest",
        submission_id=109,
    )
    assert outcome.status == SubmissionStatus.WRONG_ANSWER
    assert outcome.hardcoding is not None
    assert outcome.hardcoding["status"] == "REVIEW_RECOMMENDED"
    assert outcome.hardcoding["confidence"] is None
    assert outcome.hardcoding["evidence"]
    assert_no_leftover_workspaces(settings)


def test_custom_run_without_expected_output_never_fails_comparison(
    settings, fixture_source
) -> None:
    outcome = judge_submission(
        fixture_source("sum_correct.c"),
        "c",
        specs(("2 3\n", None, True)),
        settings=settings,
        workspace_label="pytest",
        submission_id=110,
        enable_hardcoding_analysis=False,
    )
    assert outcome.status == SubmissionStatus.ACCEPTED
    assert outcome.tests[0].run.stdout.strip() == "5"


def test_program_can_write_inside_its_workspace(settings, fixture_source) -> None:
    outcome = judge_submission(
        fixture_source("file_writer.c"),
        "c",
        specs(("", "", True)),
        settings=settings,
        workspace_label="pytest",
        submission_id=111,
    )
    event_types = [e.event_type.value for e in outcome.monitor.events()]
    assert "WORKSPACE_FILE_CREATED" in event_types
    assert outcome.fingerprint["fingerprint"]["file_events"] >= 1
    assert_no_leftover_workspaces(settings)


def test_trust_never_changes_the_policy(settings, fixture_source) -> None:
    """Identical limits produce identical policies — trust is not an input."""
    first = judge_submission(
        fixture_source("sum_correct.c"),
        "c",
        specs(("2 3\n", "5\n", True)),
        settings=settings,
        problem_limits=ProblemLimits(time_limit_ms=1200, memory_limit_mb=64),
        workspace_label="pytest-a",
        submission_id=112,
    )
    second = judge_submission(
        fixture_source("sum_correct.c"),
        "c",
        specs(("2 3\n", "5\n", True)),
        settings=settings,
        problem_limits=ProblemLimits(time_limit_ms=1200, memory_limit_mb=64),
        workspace_label="pytest-b",
        submission_id=113,
    )
    assert first.policy == second.policy
    assert first.policy.timeout_ms == 1200
    assert first.policy.memory_limit_bytes == 64 * 1024 * 1024
