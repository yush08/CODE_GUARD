"""Behavior fingerprint: real measurements, explicit nulls, safe comparisons."""

from __future__ import annotations

from app.core.constants import EventType
from app.services.behavior_monitor import BehaviorMonitor
from app.services.execution_service import RunResult
from app.services.fingerprint_service import build_fingerprint, compare_fingerprints


def run(
    *,
    duration_ms: int = 100,
    cpu_time_ms: int | None = 40,
    peak_memory_bytes: int | None = 2048,
    peak_working_set_bytes: int | None = 1024,
    active_process_peak: int | None = 1,
    exit_code: int | None = 0,
) -> RunResult:
    return RunResult(
        duration_ms=duration_ms,
        cpu_time_ms=cpu_time_ms,
        peak_memory_bytes=peak_memory_bytes,
        peak_working_set_bytes=peak_working_set_bytes,
        active_process_peak=active_process_peak,
        exit_code=exit_code,
    )


def test_fingerprint_aggregates_real_measurements() -> None:
    monitor = BehaviorMonitor()
    monitor.record(EventType.PROCESS_START)
    monitor.record(EventType.WORKSPACE_FILE_CREATED, details={"name": "out.txt"})
    monitor.record(EventType.PROCESS_EXIT)

    payload = build_fingerprint(
        7, monitor, [run(duration_ms=100), run(duration_ms=50)], ["ACCEPTED", "ACCEPTED"]
    )
    fp = payload["fingerprint"]
    assert payload["submission_id"] == 7
    assert payload["analysis_type"] == "FINGERPRINT"
    assert payload["measurement_source"] == "OBSERVED"
    assert fp["execution_time_ms"] == 150
    assert fp["cpu_time_ms"] == 80
    assert fp["peak_memory_bytes"] == 2048
    assert fp["file_events"] == 1
    assert fp["test_outcomes"] == {"ACCEPTED": 2}


def test_unavailable_metrics_are_null_and_marked_unavailable() -> None:
    monitor = BehaviorMonitor()
    payload = build_fingerprint(
        1,
        monitor,
        [run(cpu_time_ms=None, peak_memory_bytes=None, active_process_peak=None)],
        ["RUNTIME_ERROR"],
    )
    fp = payload["fingerprint"]
    coverage = payload["measurements"]
    assert fp["cpu_time_ms"] is None
    assert coverage["cpu_time_ms"] == "UNAVAILABLE"
    assert fp["peak_memory_bytes"] is None
    assert coverage["peak_memory_bytes"] == "UNAVAILABLE"
    assert fp["process_count"] is None
    assert coverage["process_count"] == "UNAVAILABLE"
    # network monitoring does not exist: reported as such, never fabricated
    assert fp["network_events"] is None
    assert coverage["network_events"].startswith("UNAVAILABLE")


def test_no_runs_means_nulls_not_zeroes() -> None:
    payload = build_fingerprint(None, BehaviorMonitor(), [], [])
    fp = payload["fingerprint"]
    assert fp["exit_code"] is None
    assert fp["execution_time_ms"] == 0  # sum of zero runs is a true zero
    assert payload["measurements"]["exit_code"] == "UNAVAILABLE"


def test_violation_events_are_counted() -> None:
    monitor = BehaviorMonitor()
    monitor.record(EventType.TIME_LIMIT_EVENT)
    monitor.record(EventType.MEMORY_LIMIT_EVENT)
    payload = build_fingerprint(1, monitor, [run()], ["TIME_LIMIT_EXCEEDED"])
    assert payload["fingerprint"]["resource_violations"] == 2


def test_notes_explain_null_semantics() -> None:
    payload = build_fingerprint(1, BehaviorMonitor(), [run()], ["ACCEPTED"])
    joined = " ".join(payload["notes"]).lower()
    assert "null" in joined
    assert "never an invented value" in joined


def test_comparison_never_overstates_similarity() -> None:
    a = {"fingerprint": {"execution_time_ms": 100, "cpu_time_ms": 50,
                         "peak_memory_bytes": None, "process_count": 1}}
    b = {"fingerprint": {"execution_time_ms": 110, "cpu_time_ms": 50,
                         "peak_memory_bytes": None, "process_count": 4}}
    cmp = compare_fingerprints(a, b)
    assert "peak_memory_bytes" in cmp.unavailable_fields
    assert "execution_time_ms" in cmp.similar_fields  # within 20%
    assert "process_count" in cmp.different_fields
    assert "cpu_time_ms" in cmp.identical_fields
    assert "not evidence of cheating" in cmp.explanation.lower()
