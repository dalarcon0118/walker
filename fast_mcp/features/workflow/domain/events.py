"""domain/events.py — Typed domain event for the workflow engine.

WorkflowEvent is the typed contract for traceability (ADR-WALKER-067 F5/I).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkflowEvent:
    """Typed event emitted by the engine (traceability / audit)."""
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    task_id: str = ""
    session_id: str = ""
    chain_dir: str = ""

    @classmethod
    def of(
        cls,
        event_type: str,
        payload: dict[str, Any] | None = None,
        **kw: Any,
    ) -> "WorkflowEvent":
        from datetime import datetime, timezone
        return cls(
            event_type=event_type,
            payload=dict(payload or {}),
            timestamp=kw.pop("timestamp", datetime.now(timezone.utc).isoformat()),
            **kw,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "chain_dir": self.chain_dir,
        }
