"""Execution of one compiled program under Windows resource controls.

Pipeline (single test run):

1. create + configure a Windows Job Object (memory / active processes /
   CPU time / kill-on-close / die-on-unhandled-exception),
2. start the program (breakaway from any inherited job, no console window),
3. assign it to our job,
4. drain stdout/stderr with hard byte caps,
5. wait on the kernel deadline (WaitForSingleObject) while a monitor thread
   samples job accounting,
6. on deadline/policy violation, terminate the whole tree via the job,
7. measure everything the OS offers, record behavior events, verify child
   cleanup, close handles.

Honesty rules baked into this module:

* ``enforcement`` states, per limit, whether it is ENFORCED by the kernel,
  only MEASURED, or UNAVAILABLE. Nothing is ever reported as enforced when
  it is merely measured.
* If job assignment fails, the run continues in measurement-only mode with
  the limitation recorded on the result and in the behavior events.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.core.constants import EventSource, EventType, Severity
from app.services.behavior_monitor import BehaviorMonitor, EventRecord
from app.services.filesystem_isolation import JUDGE_ARTIFACTS, Workspace
from app.services.process_support import (
    CREATE_BREAKAWAY_FROM_JOB,
    CREATE_NO_WINDOW,
    build_child_environment,
    close_handle,
    open_process_handle,
    start_stream_capture,
    join_streams,
    terminate_process,
    wait_with_deadline,
)
from app.services.resource_monitor import MonitorSummary, ResourceMonitor
from app.services.windows_job_manager import (
    JobLimits,
    JobLike,
    create_job,
    query_peak_working_set,
    query_process_times,
)

logger = logging.getLogger(__name__)

# Exit codes used when the judge kills a tree (students can distinguish them).
EXIT_TIME_LIMIT = 42
EXIT_PROCESS_LIMIT = 43
EXIT_OUTPUT_LIMIT = 44
EXIT_CLEANUP = 45

#: A run whose peak committed memory reaches this fraction of the configured
#: limit is classified as having hit the memory limit. Documented heuristic:
#: the kernel caps allocation at 100%, so a program pushing past ~90% was
#: effectively stopped by the limit.
MEMORY_LIMIT_HIT_RATIO = 0.9
CPU_LIMIT_HIT_RATIO = 0.9

#: NTSTATUS codes Windows uses when a console program cannot load.
DLL_FAILURE_STATUSES = {0xC0000135, 0xC0000142}


@dataclass(frozen=True)
class ExecutionPolicy:
    """Concrete limits for one submission run (never influenced by trust)."""

    timeout_ms: int
    memory_limit_bytes: int
    active_process_limit: int
    max_output_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "timeout_ms": self.timeout_ms,
            "memory_limit_bytes": self.memory_limit_bytes,
            "active_process_limit": self.active_process_limit,
            "max_output_bytes": self.max_output_bytes,
        }


@dataclass
class RunResult:
    """Everything observed during one execution."""

    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    duration_ms: int = 0
    # Time spent inside CreateProcess (antivirus first-use analysis can add
    # seconds here). Kept separate so duration_ms stays honest execution time.
    spawn_ms: int | None = None
    timed_out: bool = False
    kill_reason: str | None = None
    memory_limit_hit: bool = False
    process_limit_hit: bool = False
    output_limit_hit: bool = False
    cpu_time_exceeded: bool = False
    # measurements (None = not measured; never fabricated)
    cpu_time_ms: int | None = None
    cpu_kernel_time_ms: int | None = None
    peak_memory_bytes: int | None = None
    peak_working_set_bytes: int | None = None
    active_process_peak: int | None = None
    total_processes: int | None = None
    terminated_processes: int | None = None
    page_faults: int | None = None
    # capability honesty
    job_available: bool = False
    job_assigned: bool = False
    job_detail: str = ""
    spawn_error: str | None = None
    enforcement: dict[str, str] = field(default_factory=dict)
    monitor_summary: MonitorSummary | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


def _normalize_exit_code(raw: int | None) -> int | None:
    """Convert unsigned NTSTATUS-style codes to signed 32-bit values."""
    if raw is None:
        return None
    return raw - 0x100000000 if raw >= 0x80000000 else raw


def run_program(
    workspace: Workspace,
    exe_path: Path,
    stdin_data: str,
    policy: ExecutionPolicy,
    *,
    language: str = "c",
    settings: Settings | None = None,
    monitor: BehaviorMonitor | None = None,
    extra_env_paths: list[str] | None = None,
) -> tuple[RunResult, BehaviorMonitor]:
    """Run one program once. Returns (result, monitor) — the caller keeps the
    monitor because it owns the chronological event list."""
    settings = settings or get_settings()
    monitor = monitor or BehaviorMonitor()

    # ``cpu_time_limit_ms`` configures JOB_OBJECT_LIMIT_JOB_TIME. It is kept
    # because it can only help, but it is NOT reported as enforced: on this
    # machine repeated experiments showed the kernel CPU-time cap firing
    # late or not at all (a busy loop ran 5.9 s of CPU against a 500 ms
    # budget in 2 of 3 runs, while a control run with no job was never
    # killed). CPU-bound programs are therefore stopped by the wall-clock
    # deadline below, which IS verified reliable — and CPU usage is measured
    # post-run (see CPU_LIMIT_HIT_RATIO) and reported as MEASURED_ONLY.
    job: JobLike = create_job(
        JobLimits(
            memory_limit_bytes=policy.memory_limit_bytes,
            active_process_limit=policy.active_process_limit,
            cpu_time_limit_ms=policy.timeout_ms,
        )
    )

    before_files = workspace.snapshot_files()
    stdin_path = workspace.write_text(
        "stdin.txt", stdin_data, max_bytes=policy.max_output_bytes
    )

    # ``spawn_t0``/``t0`` bracket CreateProcess. On this machine antivirus
    # first-use analysis of a freshly compiled binary has been observed to
    # stall inside CreateProcess for 12-17 s (see README "Environment
    # observations"). That stall is NOT student execution time, so it is
    # recorded separately as ``spawn_ms`` and excluded from ``duration_ms``.
    spawn_t0 = time.monotonic()
    started_at = datetime.now(timezone.utc)
    t0 = spawn_t0

    result = RunResult(
        job_available=job.available,
        job_detail=job.detail,
        started_at=started_at,
    )

    result.enforcement = {
        "wall_clock_deadline": "ENFORCED",
        "memory_limit": "MEASURED_ONLY",
        "active_process_limit": "MEASURED_ONLY",
        "cpu_time_limit": "MEASURED_ONLY",
        "output_size_limit": "ENFORCED",
        "child_process_cleanup": "PARTIAL",
    }

    # -- spawn --------------------------------------------------------------
    # The compiler's bin directory is added so MinGW runtime DLLs (libgcc,
    # libstdc++, winpthread) resolve when the submitted C++ program starts.
    env = build_child_environment(extra_env_paths)
    proc = None
    last_error: OSError | None = None
    breakaway_used = False
    for flags, use_breakaway in (
        (CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB, True),
        (CREATE_NO_WINDOW, False),
    ):
        try:
            # Popen dups the handle, so we close ours right after the spawn.
            with open(stdin_path, "rb") as stdin_file:
                proc = subprocess.Popen(
                    [str(exe_path)],
                    cwd=str(workspace.path),
                    stdin=stdin_file,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    creationflags=flags,
                )
            breakaway_used = use_breakaway
            break
        except OSError as exc:
            last_error = exc
    if proc is None:
        result.spawn_error = f"{last_error.__class__.__name__}: {last_error}"
        result.finished_at = datetime.now(timezone.utc)
        result.duration_ms = int((time.monotonic() - t0) * 1000)
        monitor.record(
            EventType.EXECUTION_ERROR,
            severity=Severity.HIGH,
            details={"phase": "spawn", "error": result.spawn_error},
        )
        monitor.record(
            EventType.PROCESS_EXIT,
            details={"exit_code": None, "note": "process never started"},
        )
        return result, monitor

    handle = open_process_handle(proc.pid)
    # The process exists now: start the observation window. Everything spent
    # inside CreateProcess is reported as spawn_ms instead of execution time,
    # and the resource deadline below still gets its full budget.
    result.spawn_ms = int((time.monotonic() - spawn_t0) * 1000)
    t0 = time.monotonic()
    result.started_at = datetime.now(timezone.utc)
    assigned, assign_reason = (False, "OpenProcess failed")
    if handle:
        assigned, assign_reason = job.assign_process(handle)
    result.job_assigned = bool(assigned and job.available)

    if job.available and result.job_assigned:
        result.enforcement.update(
            {
                "memory_limit": "ENFORCED",
                "active_process_limit": "ENFORCED",
                "child_process_cleanup": "ENFORCED",
                # cpu_time_limit stays MEASURED_ONLY on purpose: the kernel
                # per-job CPU-time cap is configured but was verified
                # unreliable, so claiming enforcement here would be false.
            }
        )
    elif not job.available:
        result.enforcement["child_process_cleanup"] = "PARTIAL"

    monitor.record(
        EventType.PROCESS_START,
        details={
            "pid": proc.pid,
            "language": language,
            "policy": policy.as_dict(),
            "spawn_ms": result.spawn_ms,
            "job_assigned": result.job_assigned,
            "job_available": job.available,
            "breakaway_from_inherited_job": breakaway_used,
            **({"job_assignment_note": assign_reason} if assign_reason else {}),
        },
    )

    # -- capture + monitoring ----------------------------------------------
    cap_hit = threading.Event()

    def on_output_cap() -> None:
        cap_hit.set()
        monitor.record(
            EventType.OUTPUT_LIMIT_EVENT,
            severity=Severity.MEDIUM,
            details={"cap_bytes": policy.max_output_bytes, "action": "terminating"},
        )
        job.terminate(EXIT_OUTPUT_LIMIT)

    stdout_cap, stderr_cap, stream_threads = start_stream_capture(
        proc, policy.max_output_bytes, on_cap=on_output_cap
    )

    monitor_thread = ResourceMonitor(
        job,
        monitor,
        process_limit=policy.active_process_limit,
        on_process_limit=lambda: job.terminate(EXIT_PROCESS_LIMIT),
    )
    monitor_thread.start()

    # -- deadline wait (kernel event, not polling) -------------------------
    if handle:
        wait_result = wait_with_deadline(handle, policy.timeout_ms)
    else:  # pragma: no cover - only if OpenProcess failed
        try:
            proc.wait(timeout=policy.timeout_ms / 1000)
            wait_result = "exited"
        except subprocess.TimeoutExpired:
            wait_result = "timeout"

    if wait_result == "timeout":
        result.timed_out = True
        result.kill_reason = "deadline"
        monitor.record(
            EventType.TIME_LIMIT_EVENT,
            severity=Severity.MEDIUM,
            details={
                "timeout_ms": policy.timeout_ms,
                "enforcement": "WaitForSingleObject deadline + TerminateJobObject",
            },
        )
        job.terminate(EXIT_TIME_LIMIT)
        if handle:
            if wait_with_deadline(handle, 5000) != "exited":
                terminate_process(handle, EXIT_TIME_LIMIT)
                wait_with_deadline(handle, 2000)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        proc.kill()
        proc.wait(timeout=5)

    monitor_thread.stop()
    monitor_thread.join(timeout=2.0)

    result.exit_code = _normalize_exit_code(proc.returncode)
    result.duration_ms = int((time.monotonic() - t0) * 1000)
    result.stdout = stdout_cap.text
    result.stderr = stderr_cap.text
    result.stdout_truncated = stdout_cap.truncated
    result.stderr_truncated = stderr_cap.truncated
    result.monitor_summary = monitor_thread.summary

    # -- child cleanup BEFORE draining streams: orphaned children inherit the
    #    stdout/stderr pipe handles, so the drain threads only see EOF after
    #    the whole tree is gone.
    acct_before = job.query_accounting()
    active_before_cleanup = acct_before.active_processes if acct_before is not None else None
    job.terminate(EXIT_CLEANUP)
    if handle and wait_with_deadline(handle, 1000) != "exited":
        terminate_process(handle, EXIT_CLEANUP)
    # give the kernel a moment to retire orphans, then confirm
    active_after_cleanup = None
    for _ in range(20):
        acct_after = job.query_accounting()
        if acct_after is not None:
            active_after_cleanup = acct_after.active_processes
            if active_after_cleanup == 0:
                break
        time.sleep(0.05)
    join_streams(stream_threads, timeout_s=2.0)

    # -- measurements -------------------------------------------------------
    accounting = job.query_accounting()
    peak_process, peak_job = job.query_peak_memory()
    result.peak_working_set_bytes = query_peak_working_set(handle) if handle else None
    if accounting is not None:
        result.cpu_time_ms = (accounting.total_user_time_ms or 0) + (
            accounting.total_kernel_time_ms or 0
        )
        result.cpu_kernel_time_ms = accounting.total_kernel_time_ms
        result.total_processes = accounting.total_processes
        result.terminated_processes = accounting.total_terminated_processes
        result.page_faults = accounting.total_page_fault_count
    else:
        if handle:
            cpu_total, cpu_kernel = query_process_times(handle)
            result.cpu_time_ms = cpu_total
            result.cpu_kernel_time_ms = cpu_kernel
    result.active_process_peak = monitor_thread.summary.user_active_peak or None
    result.peak_memory_bytes = max(
        [v for v in (peak_job, result.peak_working_set_bytes) if v is not None] or [0]
    ) or None

    # -- policy-violation classification ------------------------------------
    if (
        policy.memory_limit_bytes
        and result.peak_memory_bytes is not None
        and result.peak_memory_bytes >= policy.memory_limit_bytes * MEMORY_LIMIT_HIT_RATIO
    ):
        result.memory_limit_hit = True
        monitor.record(
            EventType.MEMORY_LIMIT_EVENT,
            severity=Severity.MEDIUM,
            details={
                "configured_limit_bytes": policy.memory_limit_bytes,
                "peak_memory_bytes": result.peak_memory_bytes,
                "enforcement": result.enforcement["memory_limit"],
            },
        )

    if monitor_thread.summary.process_limit_detected:
        result.process_limit_hit = True
        result.kill_reason = result.kill_reason or "process_limit"

    if cap_hit.is_set():
        result.output_limit_hit = True
        result.kill_reason = result.kill_reason or "output_limit"

    if (
        result.cpu_time_ms is not None
        and not result.timed_out
        and result.cpu_time_ms >= policy.timeout_ms * CPU_LIMIT_HIT_RATIO
    ):
        result.cpu_time_exceeded = True
        result.kill_reason = result.kill_reason or "cpu_time"
        monitor.record(
            EventType.TIME_LIMIT_EVENT,
            severity=Severity.MEDIUM,
            details={
                "kind": "cpu_time",
                "cpu_time_ms": result.cpu_time_ms,
                "limit_ms": policy.timeout_ms,
                "enforcement": result.enforcement["cpu_time_limit"],
            },
        )

    # -- close handles (tree is already cleaned up above) -------------------
    job.close()
    close_handle(handle)

    monitor.record(
        EventType.PROCESS_EXIT,
        severity=Severity.INFO,
        details={
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "kill_reason": result.kill_reason,
            "children_before_cleanup": active_before_cleanup,
            "children_after_cleanup": active_after_cleanup,
        },
    )

    # -- workspace delta (real, workspace-scope file observation) -----------
    try:
        after_files = workspace.snapshot_files()
    except OSError:  # pragma: no cover
        after_files = before_files
    created = sorted(after_files - before_files - {a.lower() for a in JUDGE_ARTIFACTS})
    for rel in created:
        monitor.record(
            EventType.WORKSPACE_FILE_CREATED,
            severity=Severity.INFO,
            details={
                "path": rel,
                "observed_by": "workspace-delta",
                "note": "Workspace scope only; OS-level file monitoring is not implemented.",
            },
        )

    result.finished_at = datetime.now(timezone.utc)
    return result, monitor
