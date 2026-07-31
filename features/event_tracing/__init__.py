"""
features/event_tracing/ — TraceGraph: historial de eventos del workflow.

Domain:     TraceEvent (ODM entity)
Application: search_traces
Infrastructure: CognitiveGraph (storage canonical) + legacy fallback

Uso:
    from features.event_tracing import search, TraceEvent

    # Via CognitiveGraph
    TraceEvent.bind(cg)
    events = TraceEvent.search("Write")

    # Via funcion legacy (soporta ambos backends)
    events = search(chain_dir, {"tool": "Write"})
"""
from features.event_tracing.application.search_service import search_traces
from features.event_tracing.infrastructure.repository import load_traces
from features.event_tracing.domain.models import TraceEvent as TraceEventModel
from features.event_tracing.entities import TraceEvent


def search(chain_dir: str, filters: dict | None = None) -> list[dict]:
    """Busca eventos en los traces.

    Primary: CognitiveGraph. Fallback: legacy files.
    """
    events = load_traces(chain_dir)
    return search_traces(events, filters or {})
