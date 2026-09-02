"""domain/values.py — Value Objects for the event_tracing domain.

Frozen dataclasses representing domain concepts without identity.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventId:
    """Unique identifier for a trace event."""
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("EventId cannot be empty")


@dataclass(frozen=True)
class EventType:
    """Type of execution event."""
    VALID = {
        "workflow_state", "task_execute", "agent_message",
        "tool_call", "file_impact", "kg_query", "event",
        "error", "submit", "transition",
    }
    value: str

    def __post_init__(self) -> None:
        if self.value not in self.VALID:
            raise ValueError(f"Invalid EventType: {self.value}. Valid: {sorted(self.VALID)}")


@dataclass(frozen=True)
class EventSource:
    """Source of the event."""
    VALID = {"workflow", "agent_hook", "walker_task", "walker"}
    value: str

    def __post_init__(self) -> None:
        if self.value not in self.VALID:
            raise ValueError(f"Invalid EventSource: {self.value}. Valid: {sorted(self.VALID)}")


@dataclass(frozen=True)
class TraceFilters:
    """Search filters for the TraceGraph."""
    event_type: str = ""
    source: str = ""
    sprint_id: str = ""
    task_id: str = ""
    node: str = ""
    tool: str = ""
    max_results: int = 50

    def __post_init__(self) -> None:
        if self.max_results < 1:
            raise ValueError("TraceFilters.max_results must be >= 1")
