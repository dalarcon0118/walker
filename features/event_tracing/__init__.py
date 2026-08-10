"""
features/event_tracing/ — TraceGraph: historial de eventos del workflow.

Domain:     TraceEvent (dataclass — SSOT del modelo, ADR-WALKER-018)
Application: search_traces (fail-closed)
Infrastructure: CognitiveTraceRepository (indice trace canonical)

Uso:
    from features.event_tracing import search, TraceEvent

    # Buscar eventos (canonical, deduped)
    events = search(chain_dir, {"tool": "Write"})
"""
from features.event_tracing.application.search_service import search_traces
from features.event_tracing.infrastructure.repository import load_traces
from features.event_tracing.domain.models import TraceEvent

__all__ = ["search", "load_traces", "search_traces", "TraceEvent"]


def search(chain_dir: str, filters: dict | None = None) -> list[dict]:
    """Busca eventos en los traces.

    Primary: CognitiveGraph (indice trace canonical, deduped, ADR-WALKER-018).
    """
    events = load_traces(chain_dir)
    return search_traces(events, filters or {})