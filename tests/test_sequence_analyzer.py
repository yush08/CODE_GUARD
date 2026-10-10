"""Sequence-aware analysis: ordered rules, explanations, JSON overrides."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.constants import (
    DEFAULT_SEQUENCE_RULES,
    EventSource,
    EventType,
    RiskLevel,
    Severity,
)
from app.services import sequence_analyzer
from app.services.behavior_monitor import EventRecord

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def rec(event_type: EventType, index: int) -> EventRecord:
    return EventRecord(
        event_type=event_type,
        occurred_at=BASE + timedelta(milliseconds=index * 10),
        source=EventSource.OBSERVED,
        severity=Severity.INFO,
        details={"i": index},
        seq=index,
    )


def test_normal_start_exit_sequence_is_low_risk() -> None:
    analysis = sequence_analyzer.analyze(
        [rec(EventType.PROCESS_START, 0), rec(EventType.PROCESS_EXIT, 1)]
    )
    assert analysis.highest_risk == RiskLevel.NONE
    assert analysis.primary is not None
    assert analysis.primary.rule_id == "NORMAL_SINGLE_PROCESS"
    assert sequence_analyzer.alertable_matches(analysis) == []


def test_process_limit_sequence_creates_alertable_match() -> None:
    events = [
        rec(EventType.PROCESS_START, 0),
        rec(EventType.CHILD_PROCESS_CREATED, 1),
        rec(EventType.CHILD_PROCESS_CREATED, 2),
        rec(EventType.PROCESS_LIMIT_EVENT, 3),
        rec(EventType.PROCESS_EXIT, 4),
    ]
    analysis = sequence_analyzer.analyze(events)
    ids = {m.rule_id for m in analysis.matches}
    assert "REPEATED_CHILD_PROCESS_CREATION" in ids
    assert "PROCESS_LIMIT_THEN_TERMINATION" in ids
    assert analysis.highest_risk == RiskLevel.HIGH
    alertable = sequence_analyzer.alertable_matches(analysis)
    assert alertable, "a HIGH-risk limit termination must be reviewable"
    match = next(m for m in alertable if m.rule_id == "PROCESS_LIMIT_THEN_TERMINATION")
    assert match.explanation
    assert match.evidence and match.evidence[0]["event_type"] == "PROCESS_LIMIT_EVENT"
    assert match.evidence[0]["source"] == "OBSERVED"


def test_order_matters() -> None:
    """Same events, different order -> the ordered rule no longer matches."""
    forward = sequence_analyzer.analyze(
        [
            rec(EventType.TIME_LIMIT_EVENT, 0),
            rec(EventType.PROCESS_EXIT, 1),
        ]
    )
    assert any(m.rule_id == "TIME_LIMIT_TERMINATION" for m in forward.matches)

    backward = sequence_analyzer.analyze(
        [
            rec(EventType.PROCESS_EXIT, 0),
            rec(EventType.TIME_LIMIT_EVENT, 1),
        ]
    )
    assert not any(m.rule_id == "TIME_LIMIT_TERMINATION" for m in backward.matches)


def test_unfamiliar_sequence_is_not_treated_as_malicious() -> None:
    analysis = sequence_analyzer.analyze(
        [rec(EventType.WORKSPACE_FILE_CREATED, 0)]
    )
    assert analysis.highest_risk == RiskLevel.NONE
    assert analysis.matches == []
    assert "no configured baseline rule matched" in analysis.explanation.lower()
    assert "not treated as malicious" in analysis.explanation.lower()


def test_events_are_sorted_chronologically_before_matching() -> None:
    events = [
        rec(EventType.PROCESS_EXIT, 5),
        rec(EventType.PROCESS_START, 0),
    ]
    analysis = sequence_analyzer.analyze(events)
    assert analysis.observed_events == [
        EventType.PROCESS_START.value,
        EventType.PROCESS_EXIT.value,
    ]


def test_as_dict_shape_is_stable_for_the_api() -> None:
    payload = sequence_analyzer.analyze(
        [rec(EventType.PROCESS_START, 0), rec(EventType.PROCESS_EXIT, 1)]
    ).as_dict()
    assert set(payload) == {
        "analysis_type",
        "risk_level",
        "rule",
        "observed_events",
        "explanation",
        "source",
        "rules_evaluated",
        "matches",
    }
    assert payload["source"] == "INFERRED"
    assert payload["rules_evaluated"] == len(DEFAULT_SEQUENCE_RULES)


def test_rules_can_be_overridden_by_json_file(tmp_path) -> None:
    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps(
            [
                {
                    "rule_id": "CUSTOM_FILE_WRITE",
                    "pattern": ["WORKSPACE_FILE_CREATED", "PROCESS_EXIT"],
                    "risk_level": "LOW",
                    "explanation": "Demo override rule.",
                    "creates_alert": False,
                }
            ]
        ),
        encoding="utf-8",
    )
    rules = sequence_analyzer.load_rules(str(path))
    assert [r.rule_id for r in rules] == ["CUSTOM_FILE_WRITE"]
    analysis = sequence_analyzer.analyze(
        [
            rec(EventType.WORKSPACE_FILE_CREATED, 0),
            rec(EventType.PROCESS_EXIT, 1),
        ],
        rules=rules,
    )
    assert analysis.primary is not None
    assert analysis.primary.rule_id == "CUSTOM_FILE_WRITE"
    assert analysis.rules_evaluated == 1


def test_broken_rules_file_falls_back_to_defaults(tmp_path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    rules = sequence_analyzer.load_rules(str(path))
    assert tuple(rules) == DEFAULT_SEQUENCE_RULES


@pytest.mark.parametrize("rule", DEFAULT_SEQUENCE_RULES, ids=lambda r: r.rule_id)
def test_every_default_rule_has_an_explanation(rule) -> None:
    assert rule.explanation.strip()
    assert rule.pattern
