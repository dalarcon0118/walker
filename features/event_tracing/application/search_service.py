"""application/search_service.py — Busqueda en traces.

Diseño ADR-WALKER-018: fail-closed. Un filtro con clave desconocida NO
devuelve todo (un typo no debe ampliar el resultado); el evento no matchea.
Soporta ademas el filtro sprint_id/sprint (antes imposible a pesar de que
TraceEvent tiene el campo).
"""
from __future__ import annotations
from features.event_tracing.domain.models import TraceEvent

_KNOWN_FILTERS = {
    "tool",
    "task",
    "task_id",
    "event_type",
    "type",
    "session",
    "node",
    "source",
    "sprint_id",
    "sprint",
}


def search_traces(events: list[TraceEvent], filters: dict) -> list[dict]:
    """Busca eventos que coincidan con los filtros.

    Filtros soportados:
      - tool:       coincide con tool usada (bash, Write, etc.)
      - task/task_id: substring en task_id
      - event_type: coincide con tipo de evento
      - type:       alias de event_type
      - session:    substring en session_id
      - node:       substring en node
      - source:     substring en source
      - sprint_id/sprint: substring en sprint_id

    Claves desconocidas -> el evento NO matchea (fail-closed).
    """
    results = []
    for ev in events:
        match = True
        for k, v in filters.items():
            if k not in _KNOWN_FILTERS:
                match = False
                break
            if k == "tool" and v not in (ev.tool or ""):
                match = False
            elif k in ("task", "task_id") and str(v) not in str(ev.task_id):
                match = False
            elif k in ("event_type", "type") and v not in (ev.event_type or ""):
                match = False
            elif k == "session" and v not in (ev.session_id or ""):
                match = False
            elif k == "node" and v not in (ev.node or ""):
                match = False
            elif k == "source" and v not in (ev.source or ""):
                match = False
            elif k in ("sprint_id", "sprint") and v not in (ev.sprint_id or ""):
                match = False
        if match:
            results.append(ev.to_dict())
    return results
