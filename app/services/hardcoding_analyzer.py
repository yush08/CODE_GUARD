"""Novelty Feature 3 — possible hardcoding / overfitting detection.

A transparent heuristic that asks: did the submission pass the visible
examples but fail additional valid tests? It produces a REVIEW
RECOMMENDATION with evidence — never an accusation, never a penalty, and
never an invented confidence percentage (``confidence`` is always null
because no validated scoring method exists).

Explicit limitations (by design):

* Passing public tests and failing hidden tests is NOT proof of hardcoding.
* The module only sees what the judge recorded: statuses, output hashes,
  and test metadata. Hidden test inputs/outputs never appear in evidence.
* Only problems with hidden or generated tests can produce a signal;
  otherwise the result is INSUFFICIENT_DATA.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable

from app.core.constants import AnalysisType, HardcodingStatus

OUTPUT_SUMMARY_LIMIT = 240


def hash_output(normalized_output: str) -> str:
    return hashlib.sha256(normalized_output.encode("utf-8")).hexdigest()


def summarize_output(normalized_output: str) -> str:
    text = normalized_output.strip()
    if len(text) > OUTPUT_SUMMARY_LIMIT:
        return text[: OUTPUT_SUMMARY_LIMIT - 1] + "…"
    return text


@dataclass(frozen=True)
class TestObservation:
    """Everything the heuristic is allowed to see about one test run."""

    test_index: int
    status: str
    is_public: bool
    is_generated: bool
    output_hash: str | None
    input_hash: str | None
    expected_hash: str | None
    label: str | None = None

    @property
    def is_hidden(self) -> bool:
        return not self.is_public

    @property
    def display_name(self) -> str:
        kind = (
            "public"
            if self.is_public
            else ("generated" if self.is_generated else "hidden")
        )
        base = f"test #{self.test_index} ({kind}"
        return f"{base}, {self.label})" if self.label else f"{base})"


@dataclass(frozen=True)
class HardcodingFinding:
    status: HardcodingStatus
    evidence: list[str]
    explanation: str
    confidence: None = None  # intentionally null: no validated scoring method
    supporting_tests: list[dict[str, Any]] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "analysis_type": AnalysisType.HARDCODING_HEURISTIC.value,
            "status": self.status.value,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "supporting_tests": self.supporting_tests or [],
            "notes": [
                "A review recommendation is not an accusation of cheating.",
                "Failing hidden tests can equally indicate an incomplete algorithm.",
                "No confidence percentage is reported; none has been validated.",
            ],
        }


def analyze(tests: Iterable[TestObservation]) -> HardcodingFinding:
    tests = list(tests)
    if len(tests) < 2:
        return HardcodingFinding(
            status=HardcodingStatus.INSUFFICIENT_DATA,
            evidence=[f"Only {len(tests)} test result(s) were recorded."],
            explanation=(
                "At least two executed tests are needed before any overfitting "
                "signal can be evaluated."
            ),
        )

    public = [t for t in tests if t.is_public]
    extra = [t for t in tests if not t.is_public or t.is_generated]
    public_failed = [t for t in public if t.status != "ACCEPTED"]

    evidence: list[str] = []
    supporting: list[dict[str, Any]] = []

    # Signal A: passed every visible example but failed additional valid tests.
    signal_a = False
    if public and not public_failed and extra:
        extra_failed = [t for t in extra if t.status != "ACCEPTED"]
        if extra_failed:
            signal_a = True
            evidence.append(
                f"Submission passed all {len(public)} public test(s) but failed "
                f"{len(extra_failed)} of {len(extra)} hidden/generated test(s)."
            )
            for t in extra_failed:
                supporting.append(
                    {"test_index": t.test_index, "kind": "generated" if t.is_generated else "hidden",
                     "label": t.label, "status": t.status}
                )

    # Signal B: identical outputs for tests whose inputs and expected answers differ.
    signal_b = False
    by_output: dict[str, list[TestObservation]] = {}
    for t in tests:
        if t.output_hash:
            by_output.setdefault(t.output_hash, []).append(t)
    for group in by_output.values():
        if len(group) < 2:
            continue
        distinct_inputs = {t.input_hash for t in group if t.input_hash}
        distinct_expected = {t.expected_hash for t in group if t.expected_hash}
        if len(distinct_inputs) >= 2 and len(distinct_expected) >= 2:
            signal_b = True
            evidence.append(
                f"Program produced identical output for {len(group)} tests with "
                "different inputs and different expected answers: "
                + ", ".join(t.display_name for t in group)
                + "."
            )
            for t in group:
                supporting.append(
                    {"test_index": t.test_index, "kind": "generated" if t.is_generated else ("public" if t.is_public else "hidden"),
                     "label": t.label, "status": t.status}
                )

    if signal_a or signal_b:
        return HardcodingFinding(
            status=HardcodingStatus.REVIEW_RECOMMENDED,
            evidence=evidence,
            explanation=(
                "Observed patterns may indicate a solution tailored to the known "
                "test cases (e.g. hardcoded outputs). They can equally indicate an "
                "incomplete algorithm. Faculty review is recommended; this is not a "
                "finding of misconduct."
            ),
            supporting_tests=supporting,
        )

    if not extra and not public_failed:
        return HardcodingFinding(
            status=HardcodingStatus.INSUFFICIENT_DATA,
            evidence=[],
            explanation=(
                "All available tests passed, but only public tests exist for this "
                "submission — there is nothing beyond the visible examples to compare."
            ),
        )

    return HardcodingFinding(
        status=HardcodingStatus.NO_SIGNAL,
        evidence=[],
        explanation=(
            "No overfitting signal detected: results across public, hidden, and "
            "generated tests show no suspicious pattern."
        ),
    )
