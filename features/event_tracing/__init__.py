"""
features/event_tracing/ — TraceGraph: historial de eventos del workflow.

Domain:     TraceEvent (dataclass — SSOT del modelo, ADR-WALKER-018)
Application: search_traces (fail-closed)
Infrastructure: CognitiveTraceRepository (indice trace canonical) + legacy
                migrado on-demand (migrate_legacy_traces, idempotente)

Uso:
    from features.event_tracing import search, TraceEvent, migrate_legacy_traces

    # Buscar eventos (canonical + legacy deduped)
    events = search(chain_dir, {"tool": "Write"})

    # Consolidar legacy al indice trace (una sola vez)
    migrated = migrate_legacy_traces(chain_dir)
"""
from features.event_tracing.application.search_service import search_traces
from features.event_tracing.infrastructure.repository import (
    load_traces,
    migrate_legacy_traces,
)
from features.event_tracing.domain.models import TraceEvent


def search(chain_dir: str, filters: dict | None = None) -> list[dict]:
    """Busca eventos en los traces.

    Primary: CognitiveGraph. Fallback: legacy (deduped, ADR-WALKER-018).
    """
    events = load_traces(chain_dir)
    return search_traces(events, filters or {})
