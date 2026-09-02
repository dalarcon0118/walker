"""domain/events.py — Domain Events for event_tracing.

Events emitted by the event_tracing feature.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EventRecorded:
    """An event was recorded in the TraceGraph."""
    event_id: str = ""
    event_type: str = ""
    source: str = ""
    sprint_id: str = ""
    task_id: str = ""
    timestamp: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("EventRecorded.event_id cannot be empty")


@dataclass
class TraceQueried:
    """The TraceGraph was queried."""
    query_id: str = ""
    filters: dict = field(default_factory=dict)
    result_count: int = 0
    timestamp: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.query_id:
            raise ValueError("TraceQueried.query_id cannot be empty")
