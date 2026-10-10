"""Subprocess support helpers shared by compilation and execution.

Design notes:

* Deadlines are enforced with the kernel wait ``WaitForSingleObject`` on the
  process handle — an event-based wait, not a polling loop. Polling is never
  the mechanism that stops a runaway program.
* stdout/stderr are drained by dedicated threads with hard byte caps so a
  runaway program cannot exhaust memory or disk by printing.
* The child environment is rebuilt from scratch: the backend's secrets
  (DATABASE_URL etc.) are NEVER inherited by submitted programs.
"""

from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SYNCHRONIZE = 0x00100000
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400

WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF

CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL


def open_process_handle(pid: int) -> int | None:
    """Open a handle with the rights the judge needs.

    AssignProcessToJobObject requires PROCESS_SET_QUOTA | PROCESS_TERMINATE;
    waiting requires SYNCHRONIZE; measurement needs query rights.
    """
    access = (
        SYNCHRONIZE
        | PROCESS_QUERY_LIMITED_INFORMATION
        | PROCESS_QUERY_INFORMATION
        | PROCESS_TERMINATE
        | PROCESS_SET_QUOTA
    )
    handle = kernel32.OpenProcess(access, False, int(pid))
    return int(handle) if handle else None


def close_handle(handle: int | None) -> None:
    if handle:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def wait_with_deadline(handle: int, timeout_ms: int) -> str:
    """Block until the process exits or the deadline elapses.

    Returns ``"exited"`` or ``"timeout"``. Implemented on top of the Win32
    kernel wait, so it does not consume CPU while waiting and reacts the
    instant the process terminates.
    """
    result = kernel32.WaitForSingleObject(wintypes.HANDLE(handle), int(timeout_ms))
    if result == WAIT_OBJECT_0:
        return "exited"
    if result == WAIT_TIMEOUT:
        return "timeout"
    return "failed"


def terminate_process(handle: int, exit_code: int = 1) -> bool:
    """Hard-terminate a single process through its handle."""
    return bool(kernel32.TerminateProcess(wintypes.HANDLE(handle), int(exit_code)))


def wait_process_deadline(proc, timeout_ms: int) -> str:
    """Event-based wait on a Popen process. Returns "exited" or "timeout".

    Opens our own handle instead of relying on subprocess internals; falls
    back to Popen.wait only when the process vanished before OpenProcess.
    """
    handle = open_process_handle(proc.pid)
    if handle:
        try:
            return wait_with_deadline(handle, int(timeout_ms))
        finally:
            close_handle(handle)
    try:
        proc.wait(timeout=0.1)
        return "exited"
    except Exception:
        return "exited"


def build_child_environment(extra_paths: list[str] | None = None) -> dict[str, str]:
    """Minimal environment for untrusted programs.

    Deliberately excludes every backend secret (DATABASE_URL, JWT keys, ...).
    Only what a normal C/C++ console program needs is provided.
    """
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    path_parts = [
        str(Path(system_root) / "System32"),
        system_root,
    ]
    for extra in extra_paths or []:
        if extra:
            path_parts.append(str(extra))
    env = {
        "SystemRoot": system_root,
        "windir": system_root,
        "SYSTEMDRIVE": os.environ.get("SYSTEMDRIVE", "C:"),
        "TEMP": os.environ.get("TEMP", system_root),
        "TMP": os.environ.get("TMP", system_root),
        "PATH": ";".join(path_parts),
    }
    return env


# ---------------------------------------------------------------------------
# Bounded output capture
# ---------------------------------------------------------------------------
class StreamCapture:
    """Byte-counting, size-capped accumulator for one output stream."""

    def __init__(self, cap_bytes: int) -> None:
        self.cap_bytes = cap_bytes
        self.data = b""
        self.truncated = False
        self.byte_count = 0
        self._lock = threading.Lock()

    def feed(self, chunk: bytes) -> bool:
        """Add a chunk. Returns False when the cap was hit (stop reading)."""
        with self._lock:
            self.byte_count += len(chunk)
            remaining = self.cap_bytes - len(self.data)
            if remaining > 0:
                self.data += chunk[:remaining]
            if self.byte_count >= self.cap_bytes:
                self.truncated = True
                return False
            return True

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", errors="replace")


class _DrainThread(threading.Thread):
    def __init__(self, stream, capture: StreamCapture, on_cap: "callable | None") -> None:
        super().__init__(daemon=True, name="codeguard-stream-drain")
        self._stream = stream
        self._capture = capture
        self._on_cap = on_cap

    def run(self) -> None:
        try:
            while True:
                chunk = self._stream.read(65536)
                if not chunk:
                    break
                if not self._capture.feed(chunk):
                    if self._on_cap is not None:
                        self._on_cap()
                    break
        except (OSError, ValueError):
            pass
        finally:
            try:
                self._stream.close()
            except OSError:
                pass


def start_stream_capture(popen, cap_bytes: int, on_cap: "callable | None" = None) -> tuple[StreamCapture, StreamCapture, list[_DrainThread]]:
    """Start bounded drain threads for the child's stdout and stderr."""
    stdout_cap = StreamCapture(cap_bytes)
    stderr_cap = StreamCapture(cap_bytes)
    threads = [
        _DrainThread(popen.stdout, stdout_cap, on_cap),
        _DrainThread(popen.stderr, stderr_cap, on_cap),
    ]
    for t in threads:
        t.start()
    return stdout_cap, stderr_cap, threads


def join_streams(threads: list[_DrainThread], timeout_s: float = 2.0) -> None:
    deadline = timeout_s
    for t in threads:
        t.join(timeout=max(deadline, 0.1))
