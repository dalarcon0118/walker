"""domain/events.py — Evento de dominio TIPADO del motor.

ADR-WALKER-067 F5/I: `WorkflowEvent` es el contrato tipado de trazabilidad
(event_type + payload validado). `ITracer.trace_event(event)` lo consume;
`trace(event_type, data)` queda como transporte de compat mientras se migran
los emit-sites.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shared.core.engine.domain.enums import DomainEventType


@dataclass(frozen=True)
class WorkflowEvent:
    """Evento tipado emitido por el motor (trazabilidad / auditoría)."""

    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    task_id: str = ""
    session_id: str = ""

    @classmethod
    def of(
        cls,
        event_type: DomainEventType | str,
        payload: dict[str, Any] | None = None,
        timestamp: str = "",
        task_id: str = "",
        session_id: str = "",
    ) -> "WorkflowEvent":
        """Factory: normaliza el tipo (enum o str) y el timestamp."""
        from datetime import datetime, timezone

        return cls(
            event_type=event_type.value if isinstance(event_type, DomainEventType) else event_type,
            payload=dict(payload or {}),
            timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
            task_id=task_id,
            session_id=session_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
            "task_id": self.task_id,
            "session_id": self.session_id,
        }