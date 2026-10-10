"""Behavior event collection for one submission run.

Events are in-memory records with explicit provenance:

* OBSERVED   — directly recorded by the execution engine (real measurements).
* INFERRED   — derived later by an analysis rule from OBSERVED data.
* SIMULATED  — synthetic demo data; NEVER used for real executions.

Event ordering is stable: records carry a monotonic sequence number so ties
on timestamp keep their true insertion order.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.constants import EventSource, EventType, Severity


@dataclass(frozen=True)
class EventRecord:
    event_type: EventType
    occurred_at: datetime
    source: EventSource
    severity: Severity
    details: dict[str, Any]
    seq: int = field(compare=True)

    @property
    def type_name(self) -> str:
        return self.event_type.value


class BehaviorMonitor:
    """Thread-safe collector of runtime events for a single execution."""

    def __init__(self) -> None:
        self._events: list[EventRecord] = []
        self._lock = threading.Lock()
        self._seq = 0

    def record(
        self,
        event_type: EventType,
        *,
        source: EventSource = EventSource.OBSERVED,
        severity: Severity = Severity.INFO,
        details: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> EventRecord:
        with self._lock:
            self._seq += 1
            record = EventRecord(
                event_type=event_type,
                occurred_at=occurred_at or datetime.now(timezone.utc),
                source=source,
                severity=severity,
                details=details or {},
                seq=self._seq,
            )
            self._events.append(record)
            return record

    def events(self) -> list[EventRecord]:
        """Chronologically ordered events (stable for equal timestamps)."""
        with self._lock:
            return sorted(self._events, key=lambda e: (e.occurred_at, e.seq))

    def count(self, event_type: EventType) -> int:
        with self._lock:
            return sum(1 for e in self._events if e.event_type == event_type)

    def to_rows(self, submission_id: int) -> list[dict[str, Any]]:
        """Persistence-ready dictionaries (no DB dependency here)."""
        return [
            {
                "submission_id": submission_id,
                "event_type": e.event_type.value,
                "occurred_at": e.occurred_at,
                "source": e.source.value,
                "severity": e.severity.value,
                "details_json": e.details,
            }
            for e in self.events()
        ]
