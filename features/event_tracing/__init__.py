"""
features/event_tracing/ — TraceGraph: historial de eventos del workflow.

Domain:     TraceEvent (dataclass — SSOT del modelo, ADR-WALKER-018)
Application: search_traces (fail-closed), build_reflection_context (reflexión)
Infrastructure: CognitiveTraceRepository (indice trace canonical)

Composition root: los consumidores importan desde la RAIZ del paquete
(from features.event_tracing import search, trace_repository), NUNCA desde
la ruta interna application/infrastructure. Si la implementacion cambia
de lugar o se reemplaza, solo se actualiza este modulo.

Uso:
    from features.event_tracing import search, TraceEvent

    # Buscar eventos (canonical, deduped)
    events = search(chain_dir, {"tool": "Write"})
"""
from typing import Any

from features.event_tracing.application.search_service import search_traces
from features.event_tracing.infrastructure.repository import load_traces
from features.event_tracing.domain.models import TraceEvent
from features.event_tracing.application.reflection import build_reflection_context

__all__ = [
    "search",
    "load_traces",
    "search_traces",
    "TraceEvent",
    "build_reflection_context",
    "trace_repository",
]


def search(chain_dir: str, filters: dict | None = None) -> list[dict]:
    """Busca eventos en los traces.

    Primary: CognitiveGraph (indice trace canonical, deduped, ADR-WALKER-018).
    """
    events = load_traces(chain_dir)
    return search_traces(events, filters or {})


def trace_repository() -> Any:
    """Factory del repo Trace (CognitiveTraceRepository por defecto).

    Punto unico de construccion: los consumidores reciben el repo sin
    importar infraestructura concreta. Si la implementacion cambia, solo
    se toca esta funcion.
    """
    from features.event_tracing.infrastructure.repository import (
        CognitiveTraceRepository,
    )

    return CognitiveTraceRepository()