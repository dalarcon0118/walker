"""domain/models.py — Eventos del TraceGraph (memoria de ejecucion).

Cada evento representa una accion atomica en el ecosistema.
Puede venir del workflow, del agente, o de herramientas externas.
Soporta dos formatos de almacenamiento:
  - trace.json.gz (index con docs array — CognitiveGraph)
  - data/traces/*.json.gz (NDJSON por sprint)

Migrated from scripts/tracegraph/domain/models.py (TASK-005).
Fixes:
  - Agregado from_ndjson() classmethod para parsear NDJSON
  - Agregado from_dict() para formato index
  - Agregado field 'tool' para compatibilidad con datos reales
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TraceEvent:
    """Evento de ejecucion en el TraceGraph.

    Responde: que paso, quien, cuando, donde, con que, sobre que archivos.
    """

    # Identidad
    id: str = ""
    event_type: str = "event"
    # workflow_state | task_execute | agent_message | tool_call | file_impact | kg_query
    source: str = "walker"             # workflow | agent_hook | walker_task

    # Quien
    agent_id: str = ""
    session_id: str = ""

    # Donde en el workflow
    sprint_id: str = ""
    task_id: str = ""
    node: str = ""
    sub_state: str = ""

    # Que
    tool: str = ""                     # herramienta usada (bash, Write, etc.)
    input: Any = field(default_factory=dict)
    output: Any = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    parent_type: str = ""              # sprint | project
    parent_id: str = ""

    # Cuando
    timestamp: str = field(default_factory=_now)
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0

    # Contexto adicional
    metadata: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)  # raw data blob

    @classmethod
    def from_ndjson(cls, line: str) -> TraceEvent | None:
        """Parse una linea NDJSON a un TraceEvent.

        Formato esperado (data/traces/*.json.gz):
            {"event_type": "...", "tool": "...", "input": {...},
             "output": {...}, "timestamp": "...", ...}
        """
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                return None
            return cls(
                id=raw.get("id", ""),
                event_type=raw.get("event_type", "event"),
                source=raw.get("source", "walker"),
                agent_id=raw.get("agent_id", ""),
                session_id=raw.get("session_id", ""),
                sprint_id=raw.get("parent_id", ""),
                task_id=raw.get("task_id", ""),
                node=raw.get("node", ""),
                sub_state=raw.get("sub_state", ""),
                tool=raw.get("tool", ""),
                input=raw.get("input", {}),
                output=raw.get("output", {}),
                files=raw.get("files", []),
                parent_type=raw.get("parent_type", ""),
                parent_id=raw.get("parent_id", ""),
                timestamp=raw.get("timestamp", _now()),
                started_at=raw.get("started_at", ""),
                finished_at=raw.get("finished_at", ""),
                duration_ms=raw.get("duration_ms", 0),
                metadata=raw.get("metadata", {}),
                data=raw.get("data", {}),
            )
        except (json.JSONDecodeError, TypeError):
            return None

    @classmethod
    def from_dict(cls, d: dict) -> TraceEvent:
        """Convierte un dict plano a TraceEvent (formato trace.json.gz docs)."""
        return cls(
            id=d.get("id", ""),
            event_type=d.get("event_type", "event"),
            source=d.get("source", "walker"),
            agent_id=d.get("agent_id", ""),
            session_id=d.get("session_id", ""),
            sprint_id=d.get("parent_id", ""),
            task_id=d.get("task_id", ""),
            node=d.get("node", ""),
            sub_state=d.get("sub_state", ""),
            tool=d.get("tool", ""),
            input=d.get("input", {}),
            output=d.get("output", {}),
            files=d.get("files", []),
            parent_type=d.get("parent_type", ""),
            parent_id=d.get("parent_id", ""),
            timestamp=d.get("timestamp", d.get("started_at", _now())),
            started_at=d.get("started_at", ""),
            finished_at=d.get("finished_at", ""),
            duration_ms=d.get("duration_ms", 0),
            metadata=d.get("metadata", {}),
            data=d.get("data", {}),
        )

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "source": self.source,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
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
