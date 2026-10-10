"""Live resource monitor for a running job.

A single low-overhead thread polls ``QueryInformationJobObject`` (a cheap
kernel call) every few milliseconds to:

* track the student-controlled process high water (infrastructure processes
  such as ``conhost.exe`` — which Windows assigns to every console process
  inside the job — are excluded by name and never counted against policy),
* observe CHILD_PROCESS_CREATED events as real child processes appear,
* detect when the configured active-process policy is exceeded and terminate
  the tree through the job,
* sample peak job memory as a cross-check for the post-mortem query.

Timeout enforcement is NOT done here — deadlines are enforced with the
kernel wait in process_support; this thread only measures and enforces the
process-count policy. Honesty note: process counts are *sampled* at the
configured interval; extremely short-lived children between polls can be
missed, which is documented in the API docs and README.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

from app.core.constants import EventType, Severity
from app.services.behavior_monitor import BehaviorMonitor
from app.services.windows_job_manager import JobLike, process_image_name

logger = logging.getLogger(__name__)

#: Windows infrastructure processes that are never attributed to the student.
INFRASTRUCTURE_PROCESS_NAMES = frozenset({"conhost.exe", "coninput.exe"})


@dataclass
class MonitorSummary:
    polls: int = 0
    #: peak concurrently active STUDENT-controlled processes (conhost excluded)
    user_active_peak: int = 0
    #: raw ActiveProcesses from job accounting (includes conhost)
    raw_active_peak: int = 0
    total_processes_observed: int = 1
    child_process_events: int = 0
    process_limit_detected: bool = False
    process_limit_terminated: bool = False
    peak_job_memory_sampled: int | None = None
    poll_interval_ms: int = 15
    notes: list[str] = field(default_factory=list)


class ResourceMonitor(threading.Thread):
    """Polls job accounting while the submission's process tree runs."""

    def __init__(
        self,
        job: JobLike,
        monitor: BehaviorMonitor,
        *,
        process_limit: int | None,
        poll_interval_ms: int = 15,
        on_process_limit: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(daemon=True, name="codeguard-resource-monitor")
        self._job = job
        self._monitor = monitor
        self._process_limit = process_limit
        self._interval = max(poll_interval_ms, 5) / 1000.0
        self._on_process_limit = on_process_limit
        self._stop_event = threading.Event()
        self.summary = MonitorSummary(poll_interval_ms=int(self._interval * 1000))

    def stop(self) -> None:
        self._stop_event.set()

    # -- helpers ------------------------------------------------------------
    def _student_process_count(self) -> int | None:
        """Count of non-infrastructure processes currently in the job.

        Returns None when enumeration is unavailable (measurement gap, never
        an invented number).
        """
        if not self._job.available:
            return None
        pids = self._job.query_process_ids()
        if not pids:
            return 0
        count = 0
        for pid in pids:
            name = process_image_name(pid)
            if name is None:
                # Process exited between the two queries, or name unreadable:
                # count it, because we genuinely saw a process here.
                count += 1
                continue
            if name in INFRASTRUCTURE_PROCESS_NAMES:
                continue
            count += 1
        return count

    def run(self) -> None:
        max_children_seen = 0
        while not self._stop_event.is_set():
            acct = self._job.query_accounting()
            if acct is not None:
                self.summary.polls += 1
                raw_active = acct.active_processes or 0
                if raw_active > self.summary.raw_active_peak:
                    self.summary.raw_active_peak = raw_active
                self.summary.total_processes_observed = max(
                    self.summary.total_processes_observed, acct.total_processes or 1
                )

                if raw_active > 1:
                    user_active = self._student_process_count()
                else:
                    user_active = raw_active  # 0 or 1: nothing to enumerate
                if user_active is not None and user_active > self.summary.user_active_peak:
                    self.summary.user_active_peak = user_active

                # Child-process observation (sampled, conhost excluded).
                children_active = max((user_active or 0) - 1, 0)
                if children_active > max_children_seen:
                    for _ in range(children_active - max_children_seen):
                        self._monitor.record(
                            EventType.CHILD_PROCESS_CREATED,
                            details={
                                "observed_student_processes": user_active,
                                "observed_by": "job-accounting-poll",
                                "note": (
                                    "Process identity is not captured; conhost.exe "
                                    "console hosts are excluded from this count."
                                ),
                            },
                            severity=Severity.LOW,
                        )
                        self.summary.child_process_events += 1
                    max_children_seen = children_active

                if (
                    self._process_limit is not None
                    and user_active is not None
                    and user_active >= self._process_limit + 1
                    and not self.summary.process_limit_detected
                ):
                    self.summary.process_limit_detected = True
                    self._monitor.record(
                        EventType.PROCESS_LIMIT_EVENT,
                        severity=Severity.HIGH,
                        details={
                            "configured_limit": self._process_limit,
                            "observed_student_processes": user_active,
                            "kernel_hard_cap": (
                                self._job.kernel_active_cap
                                if self._job.kernel_active_cap
                                else None
                            ),
                            "action": "terminating process tree",
                        },
                    )
                    if self._on_process_limit is not None:
                        try:
                            self._on_process_limit()
                            self.summary.process_limit_terminated = True
                        except Exception:  # pragma: no cover - defensive
                            logger.exception("Process-limit termination callback failed")

            if self.summary.polls % 5 == 1:
                _lo, peak = self._job.query_peak_memory()
                if peak is not None:
                    self.summary.peak_job_memory_sampled = max(
                        self.summary.peak_job_memory_sampled or 0, peak
                    )

            self._stop_event.wait(self._interval)

        if not self._job.available:
            self.summary.notes.append(
                "Job objects unavailable; accounting-based observation disabled."
            )
        self.summary.notes.append(
            f"Student-process counts are sampled every {self.summary.poll_interval_ms} ms "
            "and exclude Windows infrastructure processes (conhost.exe)."
        )
