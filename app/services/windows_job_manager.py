"""Windows Job Object management (ctypes, kernel32) — no native helper needed.

The full Win32 surface used here is reachable directly from Python:

    CreateJobObjectW / SetInformationJobObject / AssignProcessToJobObject /
    QueryInformationJobObject / TerminateJobObject / CloseHandle

Structure layouts and constants are taken from the Windows SDK headers
(winnt.h / jobapi2.h). This module was written for 64-bit Windows 10/11.

What a Job Object actually enforces here (kernel-enforced):

* JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE — closing the last job handle kills
  every remaining process in the tree (cleanup guarantee).
* JOB_OBJECT_LIMIT_JOB_MEMORY       — the kernel refuses committed memory
  beyond the limit for the whole job (all processes).
* JOB_OBJECT_LIMIT_ACTIVE_PROCESS   — the kernel refuses to create processes
  beyond the cap (hard cap; see the note on the detection margin).
* JOB_OBJECT_LIMIT_JOB_TIME         — a cumulative user-mode CPU-time cap is
  CONFIGURED. Empirically on this machine it is unreliable: repeated tests
  showed a busy loop running 5.9 s of CPU against a 500 ms budget in 2 of 3
  runs (and killing late in others), while control runs without a job were
  never killed. It is therefore always reported as MEASURED_ONLY, and the
  wall-clock deadline is what actually stops CPU-bound programs.
* JOB_OBJECT_LIMIT_DIE_ON_UNHANDED_EXCEPTION — unhandled exceptions kill the
  process (so crashes are real crashes, not suspended dialogs).

Process limits — two layers, both real:

* The kernel cap (``ActiveProcessLimit``) is set to ``2 * limit + 4``.
  Headroom is required because Windows assigns a ``conhost.exe`` console
  host to every console process inside the job; without headroom the kernel
  would block the student program's *legitimate* children. The kernel
  guarantees nothing beyond the cap ever runs (hard backstop).
* The exact configured policy is enforced by the resource monitor, which
  enumerates the job's processes each poll, ignores infrastructure
  (conhost.exe), and terminates the tree as soon as more than ``limit``
  student-controlled processes are observed. Detection is sampling-based
  (~every 15 ms) and this is documented as such.

What a Job Object does NOT do: it is NOT a security sandbox. It does not by
itself block filesystem, registry, or network access. See README.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass, field

from app.services.process_support import close_handle

logger = logging.getLogger(__name__)

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- constants (winnt.h) ----------------------------------------------------
JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

JobObjectBasicAccountingInformation = 1
JobObjectBasicAndIoAccountingInformation = 8
JobObjectExtendedLimitInformation = 9


# --- structures -------------------------------------------------------------
class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class JOBOBJECT_BASIC_AND_IO_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicInfo", JOBOBJECT_BASIC_ACCOUNTING_INFORMATION),
        ("IoInfo", IO_COUNTERS),
    ]


class JOBOBJECT_BASIC_PROCESS_ID_LIST(ctypes.Structure):
    """JobObjectBasicProcessIdList buffer (class 3)."""

    _fields_ = [
        ("NumberOfAssignedProcesses", wintypes.DWORD),
        ("NumberOfProcessIdsInList", wintypes.DWORD),
        ("ProcessIdList", ctypes.c_size_t * 128),
    ]


# --- prototypes -------------------------------------------------------------
kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
kernel32.CreateJobObjectW.restype = wintypes.HANDLE

kernel32.SetInformationJobObject.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
]
kernel32.SetInformationJobObject.restype = wintypes.BOOL

kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL

kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateJobObject.restype = wintypes.BOOL

kernel32.QueryInformationJobObject.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryInformationJobObject.restype = wintypes.BOOL

# GetProcessTimes for CPU measurement fallback
kernel32.GetProcessTimes.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_void_p, ctypes.c_void_p,
]
kernel32.GetProcessTimes.restype = wintypes.BOOL


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


# psapi for peak working set
try:
    psapi = ctypes.WinDLL("psapi", use_last_error=True)

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    _PSAPI_AVAILABLE = True
except (AttributeError, OSError):  # pragma: no cover
    _PSAPI_AVAILABLE = False


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class JobLimits:
    """Limits requested for one job. None = not requested."""

    memory_limit_bytes: int | None = None
    active_process_limit: int | None = None
    cpu_time_limit_ms: int | None = None
    kill_on_close: bool = True
    die_on_unhandled_exception: bool = True


@dataclass(frozen=True)
class JobAccounting:
    """Snapshot from QueryInformationJobObject (whole job tree)."""

    total_user_time_ms: int | None
    total_kernel_time_ms: int | None
    total_page_fault_count: int | None
    total_processes: int | None
    active_processes: int | None
    total_terminated_processes: int | None
    peak_process_memory_used: int | None
    peak_job_memory_used: int | None


@dataclass
class WindowsJob:
    """A real Windows Job Object wrapping a kernel handle."""

    handle: int
    limits: JobLimits
    kernel_active_cap: int | None
    available: bool = True
    detail: str = "Job object created and configured."
    _closed: bool = field(default=False, repr=False)

    # -- lifecycle ----------------------------------------------------------
    @classmethod
    def create(cls, limits: JobLimits) -> "WindowsJob":
        """Create and configure a job. Falls back to an unavailable job."""
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            err = ctypes.get_last_error()
            return UnavailableJob(f"CreateJobObjectW failed (WinError {err}).")
        job = cls(handle=int(handle), limits=limits, kernel_active_cap=None)
        try:
            job._configure()
        except OSError as exc:
            job.close()
            return UnavailableJob(f"SetInformationJobObject failed: {exc}")
        return job

    def _configure(self) -> None:
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        flags = 0
        if self.limits.kill_on_close:
            flags |= JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if self.limits.die_on_unhandled_exception:
            flags |= JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        if self.limits.active_process_limit is not None:
            # Windows spawns conhost.exe (console host) inside the job for
            # every console process, so the raw kernel cap must leave headroom
            # for infrastructure processes. The exact student-facing policy is
            # enforced by the resource monitor, which counts only
            # non-infrastructure processes (see resource_monitor.py).
            self.kernel_active_cap = int(self.limits.active_process_limit) * 2 + 4
            info.BasicLimitInformation.ActiveProcessLimit = self.kernel_active_cap
            flags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        if self.limits.memory_limit_bytes is not None:
            info.JobMemoryLimit = int(self.limits.memory_limit_bytes)
            flags |= JOB_OBJECT_LIMIT_JOB_MEMORY
        if self.limits.cpu_time_limit_ms is not None:
            # PerJobUserTimeLimit is in 100-ns units (user-mode CPU time).
            info.BasicLimitInformation.PerJobUserTimeLimit = int(
                self.limits.cpu_time_limit_ms * 10_000
            )
            flags |= JOB_OBJECT_LIMIT_JOB_TIME
        info.BasicLimitInformation.LimitFlags = flags

        ok = kernel32.SetInformationJobObject(
            self.handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(err, f"SetInformationJobObject failed with WinError {err}")

    # -- process management -------------------------------------------------
    def assign_process(self, process_handle: int) -> tuple[bool, str | None]:
        """Assign an open process handle to the job."""
        if not self.available:
            return False, self.detail
        ok = kernel32.AssignProcessToJobObject(self.handle, wintypes.HANDLE(process_handle))
        if ok:
            return True, None
        err = ctypes.get_last_error()
        reason = (
            f"AssignProcessToJobObject failed (WinError {err}). "
            "On Windows 8+ nested jobs are supported; a denial usually means "
            "the process already belongs to a job with incompatible limits."
        )
        logger.warning("Job assignment failed: %s", reason)
        return False, reason

    def terminate(self, exit_code: int = 1) -> bool:
        """Terminate every process in the job (kernel-side)."""
        if not self.available or self._closed:
            return False
        return bool(kernel32.TerminateJobObject(self.handle, int(exit_code)))

    # -- accounting ---------------------------------------------------------
    def query_accounting(self) -> JobAccounting | None:
        if not self.available or self._closed:
            return None
        acct = JOBOBJECT_BASIC_AND_IO_ACCOUNTING_INFORMATION()
        ok = kernel32.QueryInformationJobObject(
            self.handle,
            JobObjectBasicAndIoAccountingInformation,
            ctypes.byref(acct),
            ctypes.sizeof(acct),
            None,
        )
        if not ok:
            return None
        b = acct.BasicInfo
        return JobAccounting(
            total_user_time_ms=b.TotalUserTime // 10_000,
            total_kernel_time_ms=b.TotalKernelTime // 10_000,
            total_page_fault_count=b.TotalPageFaultCount,
            total_processes=b.TotalProcesses,
            active_processes=b.ActiveProcesses,
            total_terminated_processes=b.TotalTerminatedProcesses,
            peak_process_memory_used=None,
            peak_job_memory_used=None,
        )

    def query_peak_memory(self) -> tuple[int | None, int | None]:
        """(peak_process_memory_used, peak_job_memory_used) from the job."""
        if not self.available or self._closed:
            return None, None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        ok = kernel32.QueryInformationJobObject(
            self.handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
            None,
        )
        if not ok:
            return None, None
        return int(info.PeakProcessMemoryUsed), int(info.PeakJobMemoryUsed)

    def query_process_ids(self) -> list[int]:
        """PIDs of every process currently assigned to the job."""
        if not self.available or self._closed:
            return []
        buf = JOBOBJECT_BASIC_PROCESS_ID_LIST()
        ok = kernel32.QueryInformationJobObject(
            self.handle,
            3,  # JobObjectBasicProcessIdList
            ctypes.byref(buf),
            ctypes.sizeof(buf),
            None,
        )
        if not ok:
            return []
        return [int(p) for p in buf.ProcessIdList[: buf.NumberOfProcessIdsInList] if p]

    def close(self) -> None:
        """Close the handle. With KILL_ON_JOB_CLOSE this also kills any
        remaining processes in the tree — the cleanup guarantee."""
        if self._closed or not self.handle:
            self._closed = True
            return
        close_handle(self.handle)
        self._closed = True


class UnavailableJob:
    """Placeholder used when job objects cannot be created on this host.

    The execution engine keeps running with measurement-only behavior and
    reports the limitation explicitly — it never pretends limits are enforced.
    """

    available = False

    def __init__(self, reason: str) -> None:
        self.detail = reason
        self.limits = JobLimits()
        self.kernel_active_cap = None

    def assign_process(self, process_handle: int) -> tuple[bool, str]:
        return False, self.detail

    def terminate(self, exit_code: int = 1) -> bool:
        return False

    def query_accounting(self) -> JobAccounting | None:
        return None

    def query_peak_memory(self) -> tuple[int | None, int | None]:
        return None, None

    def query_process_ids(self) -> list[int]:
        return []

    def close(self) -> None:
        return None


JobLike = WindowsJob | UnavailableJob


def create_job(limits: JobLimits) -> JobLike:
    """Create a configured job object (never raises)."""
    return WindowsJob.create(limits)


# ---------------------------------------------------------------------------
# Measurement helpers for a raw process handle
# ---------------------------------------------------------------------------
def query_process_times(process_handle: int) -> tuple[int | None, int | None]:
    """(cpu_time_ms total, kernel_ms) for one process via GetProcessTimes."""
    creation, exit_t, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
    ok = kernel32.GetProcessTimes(
        wintypes.HANDLE(process_handle),
        ctypes.byref(creation), ctypes.byref(exit_t),
        ctypes.byref(kernel), ctypes.byref(user),
    )
    if not ok:
        return None, None

    def ms(ft: FILETIME) -> int:
        ticks = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        return ticks // 10_000

    return ms(user) + ms(kernel), ms(kernel)


def query_peak_working_set(process_handle: int) -> int | None:
    """Peak working set (bytes) via psapi — available even after exit while
    the handle is still open."""
    if not _PSAPI_AVAILABLE:
        return None
    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    ok = psapi.GetProcessMemoryInfo(
        wintypes.HANDLE(process_handle), ctypes.byref(counters), ctypes.sizeof(counters)
    )
    if not ok:
        return None
    return int(counters.PeakWorkingSetSize)


kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL


def process_image_name(pid: int) -> str | None:
    """Executable basename for a pid (e.g. 'program.exe'), or None."""
    from app.services.process_support import (
        PROCESS_QUERY_LIMITED_INFORMATION,
        open_process_handle,
    )

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        ok = kernel32.QueryFullProcessImageNameW(
            wintypes.HANDLE(handle), 0, buffer, ctypes.byref(size)
        )
        if not ok:
            return None
        return buffer.value.split("\\")[-1].lower()
    finally:
        close_handle(int(handle))


# ---------------------------------------------------------------------------
# Capability probe (used by /system/status)
# ---------------------------------------------------------------------------
_probe_cache: tuple[bool, str] | None = None


def probe_job_objects(force: bool = False) -> tuple[bool, str]:
    """Create and configure a throwaway job to prove the capability works."""
    global _probe_cache
    if _probe_cache is not None and not force:
        return _probe_cache
    job = WindowsJob.create(
        JobLimits(
            memory_limit_bytes=256 * 1024 * 1024,
            active_process_limit=4,
            cpu_time_limit_ms=1000,
        )
    )
    result = (job.available, job.detail)
    job.close()
    _probe_cache = result
    return result
