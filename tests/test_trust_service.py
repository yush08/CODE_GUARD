"""Adaptive trust: pure scoring rules (no database required)."""

from __future__ import annotations

import pytest

from app.core.constants import (
    INITIAL_TRUST_SCORE,
    TRUST_FACTORS,
    TRUST_SCORE_MAX,
    TRUST_SCORE_MIN,
    RiskLevel,
)
from app.services import trust_service


def test_initial_score_and_range() -> None:
    assert INITIAL_TRUST_SCORE == 80
    assert trust_service.clamp_score(1000) == TRUST_SCORE_MAX
    assert trust_service.clamp_score(-5) == TRUST_SCORE_MIN


def test_clean_accepted_is_rewarded() -> None:
    score, applied = trust_service.apply_factors(80, ["CLEAN_ACCEPTED"])
    assert score == 82
    assert applied[0]["delta"] == 2


def test_wrong_answer_and_compile_error_never_penalise() -> None:
    score, applied = trust_service.apply_factors(80, ["WRONG_ANSWER", "COMPILATION_ERROR"])
    assert score == 80
    assert all(entry["delta"] == 0 for entry in applied)
    # ... and they are never recorded as history rows (delta 0 is skipped).
    assert TRUST_FACTORS["WRONG_ANSWER"].delta == 0
    assert TRUST_FACTORS["COMPILATION_ERROR"].delta == 0


def test_limit_violation_weights() -> None:
    score, _ = trust_service.apply_factors(80, ["TIME_LIMIT_VIOLATION"])
    assert score == 75
    score, _ = trust_service.apply_factors(80, ["PROCESS_LIMIT_VIOLATION"])
    assert score == 65
    score, _ = trust_service.apply_factors(80, ["SEQUENCE_ALERT_HIGH"])
    assert score == 55


def test_faculty_decisions_are_symmetric() -> None:
    confirmed, _ = trust_service.apply_factors(80, ["FACULTY_CONFIRMED_FINDING"])
    dismissed, _ = trust_service.apply_factors(80, ["FACULTY_DISMISSED_FINDING"])
    assert confirmed == 65
    assert dismissed == 95


def test_scores_are_clamped_not_wraparound() -> None:
    score, _ = trust_service.apply_factors(3, ["SEQUENCE_ALERT_HIGH"])
    assert score == TRUST_SCORE_MIN
    score, _ = trust_service.apply_factors(99, ["CLEAN_ACCEPTED", "CLEAN_ACCEPTED"])
    assert score == TRUST_SCORE_MAX


def test_unknown_factor_is_reported_not_crashing() -> None:
    score, applied = trust_service.apply_factors(80, ["NOT_A_REAL_FACTOR"])
    assert score == 80
    assert applied[0]["applied"] is False
    assert applied[0]["note"] == "unknown factor"


@pytest.mark.parametrize(
    ("score", "open_alerts", "expected"),
    [
        (80, 0, "NORMAL"),
        (70, 0, "NORMAL"),
        (69, 0, "ELEVATED"),
        (50, 0, "ELEVATED"),
        (49, 0, "HIGH"),
        (0, 0, "HIGH"),
        # An open (unreviewed) alert forces at least ELEVATED...
        (80, 1, "ELEVATED"),
        (60, 3, "ELEVATED"),
        # ...but never lowers a worse level.
        (40, 5, "HIGH"),
    ],
)
def test_monitoring_levels(score: int, open_alerts: int, expected: str) -> None:
    assert trust_service.monitoring_level(score, open_alerts) == expected


def test_judge_outcome_factor_mapping() -> None:
    assert trust_service.factors_for_judge_outcome(
        final_status="ACCEPTED",
        any_time_limit=False,
        any_memory_limit=False,
        any_process_limit=False,
        any_output_limit=False,
        sequence_risk=RiskLevel.NONE,
    ) == ["CLEAN_ACCEPTED"]

    assert trust_service.factors_for_judge_outcome(
        final_status="WRONG_ANSWER",
        any_time_limit=False,
        any_memory_limit=False,
        any_process_limit=False,
        any_output_limit=False,
        sequence_risk=RiskLevel.NONE,
    ) == []

    factors = trust_service.factors_for_judge_outcome(
        final_status="PROCESS_LIMIT_EXCEEDED",
        any_time_limit=True,
        any_memory_limit=False,
        any_process_limit=True,
        any_output_limit=False,
        sequence_risk=RiskLevel.MEDIUM,
    )
    assert "PROCESS_LIMIT_VIOLATION" in factors
    assert "TIME_LIMIT_VIOLATION" in factors
    assert "SEQUENCE_ALERT_MEDIUM" in factors
    assert "CLEAN_ACCEPTED" not in factors


def test_trust_never_inputs_execution_policy() -> None:
    """Enforcement settings derive only from limits/settings — by test."""
    from app.core.config import Settings
    from app.services.judge_service import ProblemLimits, resolve_execution_policy

    settings = Settings(_env_file=None)
    a = resolve_execution_policy(
        ProblemLimits(time_limit_ms=1500, memory_limit_mb=64, process_limit=5), settings
    )
    b = resolve_execution_policy(
        ProblemLimits(time_limit_ms=1500, memory_limit_mb=64, process_limit=5), settings
    )
    assert a == b
    assert a.timeout_ms == 1500
    assert a.memory_limit_bytes == 64 * 1024 * 1024
    assert a.active_process_limit == 5
    assert resolve_execution_policy(None, settings).timeout_ms == int(
        settings.default_execution_timeout_seconds * 1000
    )
