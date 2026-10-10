"""Hardcoding heuristic: evidence-based, confidence always null, no accusations."""

from __future__ import annotations

from app.core.constants import HardcodingStatus
from app.services import hardcoding_analyzer as ha


def obs(
    index: int,
    *,
    status: str = "ACCEPTED",
    is_public: bool = False,
    is_generated: bool = False,
    output: str = "5",
    input_value: str = "2 3",
    expected: str = "5",
    label: str | None = None,
):
    return ha.TestObservation(
        test_index=index,
        status=status,
        is_public=is_public,
        is_generated=is_generated,
        output_hash=ha.hash_output(output),
        input_hash=ha.hash_output(input_value),
        expected_hash=ha.hash_output(expected),
        label=label,
    )


def test_too_few_tests_is_insufficient_data() -> None:
    finding = ha.analyze([obs(1)])
    assert finding.status == HardcodingStatus.INSUFFICIENT_DATA
    assert finding.confidence is None


def test_signal_a_public_pass_hidden_fail() -> None:
    tests = [
        obs(1, is_public=True, label="example-1"),
        obs(2, is_public=True, label="example-2"),
        obs(3, status="WRONG_ANSWER", input_value="7 8", expected="15", label="hidden-1"),
        obs(4, status="WRONG_ANSWER", input_value="1 1", expected="2", label="hidden-2"),
    ]
    finding = ha.analyze(tests)
    assert finding.status == HardcodingStatus.REVIEW_RECOMMENDED
    assert any("public" in e and "hidden" in e for e in finding.evidence)
    assert finding.supporting_tests
    assert finding.confidence is None
    assert "not a finding of misconduct" in finding.explanation


def test_signal_b_identical_outputs_for_different_inputs() -> None:
    tests = [
        obs(1, input_value="1 1", expected="2", output="0"),
        obs(2, input_value="9 9", expected="18", output="0"),
    ]
    finding = ha.analyze(tests)
    assert finding.status == HardcodingStatus.REVIEW_RECOMMENDED
    assert any("identical output" in e for e in finding.evidence)
    assert finding.confidence is None


def test_clean_run_is_no_signal() -> None:
    tests = [
        obs(1, is_public=True, label="example"),
        obs(2, input_value="4 6", expected="10", output="10", label="hidden-1"),
        obs(3, input_value="0 0", expected="0", output="0", label="hidden-2"),
    ]
    finding = ha.analyze(tests)
    assert finding.status == HardcodingStatus.NO_SIGNAL
    assert finding.evidence == []
    assert finding.confidence is None


def test_only_public_tests_is_insufficient_data() -> None:
    tests = [
        obs(1, is_public=True, label="example-1"),
        obs(2, is_public=True, status="ACCEPTED", input_value="10 10", expected="20",
            output="20", label="example-2"),
    ]
    finding = ha.analyze(tests)
    assert finding.status == HardcodingStatus.INSUFFICIENT_DATA
    assert "only public tests" in finding.explanation.lower()


def test_as_dict_never_contains_a_confidence_value() -> None:
    payload = ha.analyze(
        [
            obs(1, is_public=True),
            obs(2, status="WRONG_ANSWER", input_value="3 4", expected="7"),
        ]
    ).as_dict()
    assert payload["confidence"] is None
    assert payload["analysis_type"] == "HARDCODING_HEURISTIC"
    assert payload["status"] in {s.value for s in HardcodingStatus}
    assert any("not an accusation" in n for n in payload["notes"])


def test_hash_and_summary_helpers() -> None:
    assert ha.hash_output("5") == ha.hash_output("5")
    assert ha.hash_output("5") != ha.hash_output("6")
    long = "x" * 500
    summary = ha.summarize_output(long)
    assert len(summary) <= ha.OUTPUT_SUMMARY_LIMIT
    assert summary.endswith("…")
