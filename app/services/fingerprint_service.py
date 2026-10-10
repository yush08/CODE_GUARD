"""Novelty Feature 1 — behavior fingerprinting.

Builds a structured summary of an execution from measurements the engine
actually collected. Missing measurements are stored as ``null`` and listed in
``measurements`` as UNAVAILABLE — nothing is invented.

Similarity between fingerprints is informational only. Two submissions with
similar fingerprints are NOT evidence of plagiarism or cheating.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.core.constants import AnalysisType, EventSource, EventType
from app.services.behavior_monitor import BehaviorMonitor
from app.services.execution_service import RunResult

#: Metric id -> short description of what it measures.
METRIC_NOTES = {
    "execution_time_ms": "Summed wall-clock duration across all test runs.",
    "cpu_time_ms": "Job accounting user+kernel CPU time for the process tree.",
    "cpu_utilization_percent": "cpu_time_ms / execution_time_ms * 100 (can exceed 100 for multi-process trees).",
    "peak_memory_bytes": "Peak committed memory of the job (fallback: peak working set).",
    "peak_working_set_bytes": "Peak resident working set of the main process (psapi).",
    "process_count": "Peak concurrently active processes observed in the job.",
    "child_process_count": "Total child processes observed across all runs (job accounting).",
    "exit_code": "Exit code of the first executed test.",
    "resource_violations": "Count of limit-violation events (time/memory/process/output).",
    "file_events": "Files created inside the submission workspace (workspace-delta only).",
    "network_events": "No network monitoring is implemented.",
    "test_outcomes": "Per-status counts of test results.",
}

_VIOLATION_EVENTS = (
    EventType.TIME_LIMIT_EVENT,
    EventType.MEMORY_LIMIT_EVENT,
    EventType.PROCESS_LIMIT_EVENT,
    EventType.OUTPUT_LIMIT_EVENT,
)


def _measured(value: Any) -> str:
    return "MEASURED" if value is not None else "UNAVAILABLE"


def build_fingerprint(
    submission_id: int | str | None,
    monitor: BehaviorMonitor,
    runs: Iterable[RunResult],
    test_status: Iterable[str],
    *,
    measurement_source: str = EventSource.OBSERVED.value,
) -> dict[str, Any]:
    """Aggregate per-run results into the submission-level fingerprint."""
    runs = list(runs)
    events = monitor.events()
    test_status = list(test_status)

    execution_time_ms = sum(r.duration_ms for r in runs)
    cpu_values = [r.cpu_time_ms for r in runs if r.cpu_time_ms is not None]
    cpu_time_ms = sum(cpu_values) if cpu_values else None
    cpu_util = (
        round(cpu_time_ms / execution_time_ms * 100, 1)
        if cpu_time_ms is not None and execution_time_ms > 0
        else None
    )
    memory_values = [r.peak_memory_bytes for r in runs if r.peak_memory_bytes is not None]
    working_set_values = [
        r.peak_working_set_bytes for r in runs if r.peak_working_set_bytes is not None
    ]
    process_peaks = [r.active_process_peak for r in runs if r.active_process_peak is not None]
    children = sum(
        (r.monitor_summary.child_process_events if r.monitor_summary else 0) for r in runs
    )
    violations = sum(1 for e in events if e.event_type in _VIOLATION_EVENTS)
    file_events = monitor.count(EventType.WORKSPACE_FILE_CREATED)

    outcomes: dict[str, int] = {}
    for status in test_status:
        outcomes[status] = outcomes.get(status, 0) + 1

    fingerprint = {
        "execution_time_ms": execution_time_ms,
        "cpu_time_ms": cpu_time_ms,
        "cpu_utilization_percent": cpu_util,
        "peak_memory_bytes": max(memory_values) if memory_values else None,
        "peak_working_set_bytes": max(working_set_values) if working_set_values else None,
        "process_count": max(process_peaks) if process_peaks else None,
        "child_process_count": children,
        "exit_code": runs[0].exit_code if runs else None,
        "resource_violations": violations,
        "file_events": file_events,
        "network_events": None,
        "test_outcomes": outcomes,
    }

    coverage = {
        "execution_time_ms": "MEASURED",
        "cpu_time_ms": _measured(cpu_time_ms),
        "cpu_utilization_percent": _measured(cpu_util),
        "peak_memory_bytes": _measured(fingerprint["peak_memory_bytes"]),
        "peak_working_set_bytes": _measured(fingerprint["peak_working_set_bytes"]),
        "process_count": _measured(fingerprint["process_count"]),
        "child_process_count": "MEASURED",
        "exit_code": _measured(fingerprint["exit_code"]),
        "resource_violations": "MEASURED",
        "file_events": "MEASURED (workspace scope only)",
        "network_events": "UNAVAILABLE (no network monitoring implemented)",
    }

    return {
        "submission_id": submission_id,
        "analysis_type": AnalysisType.FINGERPRINT.value,
        "measurement_source": measurement_source,
        "fingerprint": fingerprint,
        "measurements": coverage,
        "notes": [
            "Values are real measurements from the execution engine.",
            "null means the measurement was not available — never an invented value.",
            "Similar fingerprints are not proof of shared authorship or cheating.",
        ],
    }


@dataclass(frozen=True)
class FingerprintComparison:
    comparable_fields: list[str]
    unavailable_fields: list[str]
    identical_fields: list[str]
    similar_fields: list[str]
    different_fields: list[str]
    explanation: str


def compare_fingerprints(a: dict[str, Any], b: dict[str, Any]) -> FingerprintComparison:
    """Compare two fingerprints on shared numeric fields only.

    "Similar" = within 20% relative difference. This is informational output
    for faculty review, explicitly not a plagiarism verdict.
    """
    fa = a.get("fingerprint", a)
    fb = b.get("fingerprint", b)
    comparable: list[str] = []
    unavailable: list[str] = []
    identical: list[str] = []
    similar: list[str] = []
    different: list[str] = []

    for key in ("execution_time_ms", "cpu_time_ms", "peak_memory_bytes", "process_count"):
        va, vb = fa.get(key), fb.get(key)
        if va is None or vb is None:
            unavailable.append(key)
            continue
        comparable.append(key)
        if va == vb:
            identical.append(key)
            continue
        base = max(abs(va), abs(vb), 1)
        if abs(va - vb) / base <= 0.2:
            similar.append(key)
        else:
            different.append(key)

    return FingerprintComparison(
        comparable_fields=comparable,
        unavailable_fields=unavailable,
        identical_fields=identical,
        similar_fields=similar,
        different_fields=different,
        explanation=(
            "Comparison covers shared numeric measurements only. Similar resource "
            "usage is common for correct algorithms solving the same problem and "
            "is NOT evidence of cheating or shared authorship."
        ),
    )
