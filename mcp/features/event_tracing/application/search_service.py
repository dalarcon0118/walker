"""
application/search_service.py — SHIM de compatibilidad (ADR-WALKER-048).

La búsqueda de traces ahora vive en `use_cases/search_traces.py`
(SearchTracesUseCase, patrón canonical). Este módulo re-exporta la función
`search_traces` para backward compatibility durante la migración
incremental (ADR-WALKER-048 §6): `from
features.event_tracing.application.search_service import search_traces`
sigue funcionando.

Consumidores nuevos: usen
`features.event_tracing.application.services.trace_service.TraceService`
(Application Service, INV-013).
"""
from __future__ import annotations

from .use_cases.search_traces import search_traces, SearchTracesUseCase

__all__ = ["search_traces", "SearchTracesUseCase"]
