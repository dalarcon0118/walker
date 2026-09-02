"""domain/events.py — Domain events for delta tracking.

Each event represents a relevant fact in the lifecycle of sprints
and specs. They're recorded in the trace index for traceability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DomainEvent:
    """Base event with timestamp and source."""

    occurred_at: str = field(default_factory=_now)
    source: str = "delta_tracking"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.__class__.__name__,
            "occurred_at": self.occurred_at,
            "source": self.source,
        }


@dataclass
class SpringCreated(DomainEvent):
    """A new spring was created."""

    spring_id: str = ""
    goal: str = ""
    project_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "spring_id": self.spring_id,
            "goal": self.goal,
            "project_id": self.project_id,
        })
        return base


@dataclass
class SpringUpdated(DomainEvent):
    """A spring was updated."""

    spring_id: str = ""
    changes: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "spring_id": self.spring_id,
            "changes": self.changes,
        })
        return base


@dataclass
class SpringActivated(DomainEvent):
    """A spring was activated."""

    spring_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({"spring_id": self.spring_id})
        return base


@dataclass
class SpringClosed(DomainEvent):
    """A spring was closed."""

    spring_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({"spring_id": self.spring_id})
        return base


@dataclass
class SpecCreated(DomainEvent):
    """A new spec was created."""

    spec_id: str = ""
    spring_id: str = ""
    objective: str = ""

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "spec_id": self.spec_id,
            "spring_id": self.spring_id,
            "objective": self.objective,
        })
        return base


@dataclass
class SpecUpdated(DomainEvent):
    """A spec was updated."""

    spec_id: str = ""
    spring_id: str = ""
    changes: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "spec_id": self.spec_id,
            "spring_id": self.spring_id,
            "changes": self.changes,
        })
        return base


@dataclass
class SpecCompleted(DomainEvent):
    """A spec was completed."""

    spec_id: str = ""
    spring_id: str = ""
    result_state: str = ""

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "spec_id": self.spec_id,
            "spring_id": self.spring_id,
            "result_state": self.result_state,
        })
        return base


@dataclass
class LegacyImported(DomainEvent):
    """Legacy history was imported."""

    source_type: str = ""
    items_imported: int = 0
    project_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "source_type": self.source_type,
            "items_imported": self.items_imported,
            "project_id": self.project_id,
        })
        return base


class EventBus:
    """Simple in-memory event bus for delta domain events."""

    def __init__(self) -> None:
        self._listeners: list[callable] = []

    def subscribe(self, listener: callable) -> None:
        self._listeners.append(listener)

    def publish(self, event: DomainEvent) -> None:
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass
