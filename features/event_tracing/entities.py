"""entities.py — Entidad TraceEvent para TraceGraph (indice trace).

Uso:
    from features.event_tracing.entities import TraceEvent

    TraceEvent.bind(cg)

    # Crear
    event = TraceEvent(
        event_type="tool_call",
        tool="Write",
        task_id="TASK-042",
        files=["src/auth.py"],
    )
    event.save()

    # Buscar
    events = TraceEvent.search("Write")
    events = TraceEvent.find_by(tool="Write")
    events = TraceEvent.find_by(task_id="TASK-042")
"""

from __future__ import annotations
from libs.cognitive_graph.odm import Entity, fields


class TraceEvent(Entity):
    """TraceGraph: memoria de ejecucion (workflow + agente + tools)."""

    __index__ = "trace"
    __search__ = ["event_type", "tool"]

    event_type: str = fields.String()
    source: str = fields.String()
    agent_id: str = fields.String()
    session_id: str = fields.String()
    sprint_id: str = fields.String()
    task_id: str = fields.String()
    node: str = fields.String()
    sub_state: str = fields.String()
    tool: str = fields.Text(fulltext=True)
    files: list[str] = fields.List()
    timestamp: str = fields.String()
    duration_ms: int = fields.Int()
    metadata: dict = fields.Dict()
    data: dict = fields.Dict()
