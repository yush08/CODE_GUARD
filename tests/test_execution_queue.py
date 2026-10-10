"""Bounded execution queue: lifecycle, capacity, completion signalling."""

from __future__ import annotations

import queue as queue_mod
import threading

import pytest

from app.core.exceptions import QueueFullError
from app.services.execution_queue import ExecutionQueue


def _passive_loop(work_queue, stop_event, on_done):
    """Worker that acknowledges jobs instantly (no database involved)."""
    while not stop_event.is_set():
        try:
            submission_id = work_queue.get(timeout=0.2)
        except queue_mod.Empty:
            continue
        try:
            on_done(submission_id)
        finally:
            work_queue.task_done()


def test_lifecycle_and_completion_events(monkeypatch) -> None:
    import app.workers.execution_worker as worker_mod

    monkeypatch.setattr(worker_mod, "worker_loop", _passive_loop)
    q = ExecutionQueue()
    assert q.snapshot()["running"] is False

    q.start()
    try:
        snap = q.snapshot()
        assert snap["running"] is True
        assert snap["max_concurrent_executions"] >= 1
        assert snap["max_queued_jobs"] >= 1

        q.submit(42)
        assert q.wait_for_completion(42, timeout=5.0) is True
        # the completion event is consumed exactly once
        assert q.wait_for_completion(42, timeout=0.2) is False
    finally:
        q.shutdown()
    assert q.snapshot()["running"] is False


def test_submit_before_start_is_rejected_clearly() -> None:
    q = ExecutionQueue()
    with pytest.raises(QueueFullError):
        q.submit(1)


def test_queue_is_bounded_and_reports_capacity(monkeypatch) -> None:
    import app.workers.execution_worker as worker_mod
    from app.core.config import Settings
    import app.services.execution_queue as queue_module

    started = threading.Event()

    def blocking_loop(work_queue, stop_event, on_done):
        started.set()
        stop_event.wait(timeout=10)

    monkeypatch.setattr(worker_mod, "worker_loop", blocking_loop)
    monkeypatch.setattr(
        queue_module,
        "get_settings",
        lambda: Settings(_env_file=None, max_concurrent_executions=1, max_queued_jobs=1),
    )

    q = ExecutionQueue()
    q.start()
    try:
        assert started.wait(timeout=5), "worker did not start"
        q.submit(1)  # fills the single queue slot (the worker never drains it)
        with pytest.raises(QueueFullError) as excinfo:
            q.submit(2)
        assert excinfo.value.code == "EXECUTION_QUEUE_FULL"
        assert excinfo.value.http_status == 503
        assert excinfo.value.details["retry_after_seconds"] == 5
        # a rejected job must not linger in the completion map
        assert q.wait_for_completion(2, timeout=0.2) is False
    finally:
        q.shutdown()
