"""domain/events.py — Domain Events de event_tracing.

Eventos de dominio emitidos por el feature event_tracing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EventRecorded:
    """Se registro un evento en el TraceGraph."""
    event_id: str = ""
    event_type: str = ""
    source: str = ""
    agent_id: str = ""
    session_id: str = ""
    sprint_id: str = ""
    task_id: str = ""
    timestamp: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("EventRecorded.event_id no puede ser vacio")


@dataclass
class TraceQueried:
    """Se consulto el TraceGraph."""
    query_id: str = ""
    filters: dict = field(default_factory=dict)
    result_count: int = 0
    agent_id: str = ""
    session_id: str = ""
    timestamp: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.query_id:
            raise ValueError("TraceQueried.query_id no puede ser vacio")


@dataclass
class TraceIndexed:
    """Se indexo un directorio de traces en el TraceGraph."""
    chain_dir: str = ""
    events_indexed: int = 0
    timestamp: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.chain_dir:
            raise ValueError("TraceIndexed.chain_dir no puede ser vacio")
