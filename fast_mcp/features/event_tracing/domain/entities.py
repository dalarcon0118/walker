"""domain/entities.py — TraceEvent entity.

Core entity for execution events in the TraceGraph.
Simplified from legacy (107 LOC → ~70 LOC): removed agent_id/session_id,
kept the fields the engine actually uses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TraceEvent:
    """Evento de ejecución en el TraceGraph.

    Responde: qué pasó, cuándo, dónde, con qué, sobre qué archivos.
    """

    # Identidad
    id: str = ""
    event_type: str = "event"  # workflow_state | task_execute | tool_call | file_impact | error | submit | transition
    source: str = "walker"     # workflow | agent_hook | walker_task

    # Workflow context
    sprint_id: str = ""
    task_id: str = ""
    node: str = ""
    sub_state: str = ""

    # What happened
    tool: str = ""
    input: Any = field(default_factory=dict)
    output: Any = field(default_factory=dict)
    files: list[str] = field(default_factory=list)

    # Timing
    timestamp: str = field(default_factory=_now)
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0

    # Context
    metadata: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> TraceEvent:
        """Symmetric conversion from dict (ADR-WALKER-018)."""
        return cls(
            id=d.get("id", ""),
            event_type=d.get("event_type", "event"),
            source=d.get("source", "walker"),
            sprint_id=d.get("sprint_id") or d.get("parent_id", ""),
            task_id=d.get("task_id", ""),
            node=d.get("node", ""),
            sub_state=d.get("sub_state", ""),
            tool=d.get("tool", ""),
            input=d.get("input", {}),
            output=d.get("output", {}),
            files=d.get("files", []),
            timestamp=d.get("timestamp", d.get("started_at", _now())),
            started_at=d.get("started_at", ""),
            finished_at=d.get("finished_at", ""),
            duration_ms=d.get("duration_ms", 0),
            metadata=d.get("metadata", {}),
            data=d.get("data", {}),
        )

    def to_dict(self) -> dict:
        """Symmetric serialization (ADR-WALKER-018)."""
        return {
            "id": self.id,
            "event_type": self.event_type,
            "source": self.source,
            "sprint_id": self.sprint_id,
            "task_id": self.task_id,
            "node": self.node,
            "sub_state": self.sub_state,
            "tool": self.tool,
            "input": self.input,
            "output": self.output,
            "files": self.files,
            "timestamp": self.timestamp,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
            "data": self.data,
        }
