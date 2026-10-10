"""Novelty Feature 2 — sequence-aware behavior analysis.

Analyzes the *order* of recorded runtime events against transparent,
configurable baseline rules. A rule matches when its pattern appears as a
contiguous subsequence of the chronological event stream.

Design rules:

* Only recorded event types are ever analyzed — the module cannot and does
  not imagine events that were not observed.
* Unfamiliar sequences are NOT classified as malicious; they simply produce
  "no rule matched" with risk level NONE.
* Every finding carries the matched rule id, the supporting events, and a
  plain-language explanation.
* Rules can be overridden by a JSON file (SEQUENCE_RULES_PATH) without code
  changes; see README for the format.

A finding is an INFERRED assessment derived from OBSERVED events.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.core.constants import (
    DEFAULT_SEQUENCE_RULES,
    AnalysisType,
    EventType,
    RiskLevel,
    SequenceRule,
    Severity,
)
from app.services.behavior_monitor import EventRecord

logger = logging.getLogger(__name__)

_RISK_ORDER = {
    RiskLevel.NONE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
}


def load_rules(rules_path: str | None = None) -> tuple[SequenceRule, ...]:
    """Default rules, optionally replaced/extended by a JSON file.

    JSON format: [{"rule_id", "pattern": ["PROCESS_START", ...], "risk_level",
    "explanation", "creates_alert"}, ...]
    """
    if not rules_path:
        return DEFAULT_SEQUENCE_RULES
    path = Path(rules_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        rules: list[SequenceRule] = []
        for item in raw:
            rules.append(
                SequenceRule(
                    rule_id=str(item["rule_id"]),
                    pattern=tuple(EventType(p) for p in item["pattern"]),
                    risk_level=RiskLevel(item.get("risk_level", "LOW")),
                    explanation=str(item.get("explanation", "")),
                    creates_alert=bool(item.get("creates_alert", False)),
                )
            )
        return tuple(rules) or DEFAULT_SEQUENCE_RULES
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("Could not load sequence rules from %s (%s); using defaults.", rules_path, exc.__class__.__name__)
        return DEFAULT_SEQUENCE_RULES


def _match_pattern(haystack: Sequence[EventType], pattern: Sequence[EventType]) -> int | None:
    """Return start index of the first contiguous match, else None."""
    n, m = len(haystack), len(pattern)
    if m == 0 or m > n:
        return None
    for i in range(n - m + 1):
        if list(haystack[i : i + m]) == list(pattern):
            return i
    return None


@dataclass
class RuleMatch:
    rule_id: str
    risk_level: RiskLevel
    explanation: str
    creates_alert: bool
    matched_at_index: int
    matched_event_types: list[str]
    evidence: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "risk_level": self.risk_level.value,
            "explanation": self.explanation,
            "creates_alert": self.creates_alert,
            "matched_at_index": self.matched_at_index,
            "matched_event_types": self.matched_event_types,
            "evidence": self.evidence,
        }


@dataclass
class SequenceAnalysis:
    analysis_type: str = AnalysisType.SEQUENCE_ANALYSIS.value
    observed_events: list[str] = field(default_factory=list)
    matches: list[RuleMatch] = field(default_factory=list)
    highest_risk: RiskLevel = RiskLevel.NONE
    primary: RuleMatch | None = None
    explanation: str = ""
    source: str = "INFERRED"
    rules_evaluated: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "analysis_type": self.analysis_type,
            "risk_level": self.highest_risk.value,
            "rule": self.primary.rule_id if self.primary else None,
            "observed_events": self.observed_events,
            "explanation": self.explanation,
            "source": self.source,
            "rules_evaluated": self.rules_evaluated,
            "matches": [m.as_dict() for m in self.matches],
        }


def analyze(
    events: Iterable[EventRecord],
    rules: Iterable[SequenceRule] | None = None,
) -> SequenceAnalysis:
    """Run every rule over the chronological event stream."""
    rules = tuple(rules) if rules is not None else DEFAULT_SEQUENCE_RULES
    ordered = sorted(events, key=lambda e: (e.occurred_at, e.seq))
    stream = [e.event_type for e in ordered]
    analysis = SequenceAnalysis(
        observed_events=[e.event_type.value for e in ordered],
        rules_evaluated=len(rules),
    )

    for rule in rules:
        idx = _match_pattern(stream, rule.pattern)
        if idx is None:
            continue
        matched_events = ordered[idx : idx + len(rule.pattern)]
        analysis.matches.append(
            RuleMatch(
                rule_id=rule.rule_id,
                risk_level=rule.risk_level,
                explanation=rule.explanation,
                creates_alert=rule.creates_alert,
                matched_at_index=idx,
                matched_event_types=[e.event_type.value for e in matched_events],
                evidence=[
                    {
                        "event_type": e.event_type.value,
                        "occurred_at": e.occurred_at.isoformat(),
                        "source": e.source.value,
                        "severity": e.severity.value,
                        "details": e.details,
                    }
                    for e in matched_events
                ],
            )
        )

    if analysis.matches:
        primary = max(analysis.matches, key=lambda m: _RISK_ORDER[m.risk_level])
        analysis.primary = primary
        analysis.highest_risk = primary.risk_level
        analysis.explanation = primary.explanation
    else:
        analysis.explanation = (
            "No configured baseline rule matched the observed event sequence. "
            "Unfamiliar orderings are not treated as malicious by default."
        )

    return analysis


def alertable_matches(analysis: SequenceAnalysis) -> list[RuleMatch]:
    """Matches that should create a faculty-review alert."""
    return [m for m in analysis.matches if m.creates_alert and _RISK_ORDER[m.risk_level] >= 2]


def severity_for_risk(risk: RiskLevel) -> Severity:
    return {
        RiskLevel.NONE: Severity.INFO,
        RiskLevel.LOW: Severity.LOW,
        RiskLevel.MEDIUM: Severity.MEDIUM,
        RiskLevel.HIGH: Severity.HIGH,
    }[risk]
