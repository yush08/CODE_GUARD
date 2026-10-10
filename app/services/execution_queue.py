"""Bounded execution queue decoupling API requests from program execution.

* API handlers validate, persist a QUEUED submission, and enqueue its id —
  they never block on compilation/execution (the FastAPI event loop stays free).
* A fixed pool of worker threads (MAX_CONCURRENT_EXECUTIONS) drains a bounded
  queue (MAX_QUEUED_JOBS). When the queue is full the API returns a clear
  EXECUTION_QUEUE_FULL (503) instead of accepting unbounded work.
* Submissions run one test at a time inside a worker; concurrency is bounded
  at the submission level, so a single machine never runs more program
  processes than the configured maximum.
"""

from __future__ import annotations

import logging
import queue
import threading
import time

from app.core.config import get_settings
from app.core.exceptions import QueueFullError

logger = logging.getLogger(__name__)


class ExecutionQueue:
    """Process-wide bounded queue + worker pool (started by app lifespan)."""

    def __init__(self) -> None:
        self._queue: queue.Queue[int] | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._completion: dict[int, threading.Event] = {}
        self._running = False
        self.max_workers = 0
        self.maxsize = 0

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            settings = get_settings()
            self.max_workers = max(1, settings.max_concurrent_executions)
            self.maxsize = max(1, settings.max_queued_jobs)
            self._queue = queue.Queue(maxsize=self.maxsize)
            self._stop.clear()
            from app.workers.execution_worker import worker_loop

            for i in range(self.max_workers):
                thread = threading.Thread(
                    target=worker_loop,
                    args=(self._queue, self._stop, self._on_done),
                    name=f"codeguard-worker-{i}",
                    daemon=True,
                )
                thread.start()
                self._threads.append(thread)
            self._running = True
            logger.info(
                "Execution queue started (workers=%d, queue=%d).", self.max_workers, self.maxsize
            )

    def shutdown(self, timeout: float = 10.0) -> None:
        with self._lock:
            if not self._running:
                return
            self._stop.set()
            threads, self._threads = self._threads, []
            self._running = False
        for thread in threads:
            thread.join(timeout=timeout)
        logger.info("Execution queue stopped.")

    # -- API-facing ---------------------------------------------------------
    def submit(self, submission_id: int) -> None:
        """Enqueue a job. Raises QueueFullError (HTTP 503) when at capacity."""
        with self._lock:
            if not self._running or self._queue is None:
                raise QueueFullError()
            self._completion.setdefault(submission_id, threading.Event())
            try:
                self._queue.put_nowait(submission_id)
            except queue.Full:
                self._completion.pop(submission_id, None)
                raise QueueFullError() from None

    def wait_for_completion(self, submission_id: int, timeout: float) -> bool:
        """Block (thread-safe, off the event loop) until the job finishes."""
        with self._lock:
            event = self._completion.get(submission_id)
        if event is None:
            return False
        finished = event.wait(timeout=timeout)
        if finished:
            with self._lock:
                self._completion.pop(submission_id, None)
        return finished

    def _on_done(self, submission_id: int) -> None:
        with self._lock:
            event = self._completion.get(submission_id)
        if event is not None:
            event.set()

    # -- introspection ------------------------------------------------------
    def snapshot(self) -> dict[str, int | bool]:
        pending = self._queue.qsize() if self._queue is not None else 0
        active = 0
        for thread in self._threads:
            if thread.is_alive():
                active += 1
        with self._lock:
            in_flight = len(self._completion)
        return {
            "running": self._running,
            "queued_jobs": pending,
            "active_workers": active,
            "in_flight_submissions": in_flight,
            "max_concurrent_executions": self.max_workers,
            "max_queued_jobs": self.maxsize,
        }


execution_queue = ExecutionQueue()
