"""application/search_service.py — Busqueda en traces.

Migrated from scripts/tracegraph/application/search_service.py (TASK-005).
Fixes:
  - filter por 'tool' ahora usa TraceEvent.tool (campo existente)
  - Agregado filtro por 'event_type' (type -> event_type)
  - Importa desde features.event_tracing.domain
"""
from __future__ import annotations
from features.event_tracing.domain.models import TraceEvent


def search_traces(events: list[TraceEvent], filters: dict) -> list[dict]:
    """Busca eventos que coincidan con los filtros.

    Filtros soportados:
      - tool:       coincide con tool usada (bash, Write, etc.)
      - task:       substring en task_id
      - event_type: coincide con tipo de evento
      - type:       alias de event_type
      - session:    substring en session_id
      - node:       substring en node
    """
    results = []
    for ev in events:
        match = True
        for k, v in filters.items():
            if k == "tool" and v not in (ev.tool or ""):
                match = False
            elif k in ("task", "task_id") and str(v) not in str(ev.task_id):
                match = False
            elif k == "event_type" and v not in (ev.event_type or ""):
                match = False
            elif k == "type" and v not in (ev.event_type or ""):
                match = False
            elif k == "session" and v not in (ev.session_id or ""):
                match = False
            elif k == "node" and v not in (ev.node or ""):
                match = False
            elif k == "source" and v not in (ev.source or ""):
                match = False
        if match:
            results.append(ev.to_dict())
    return results
