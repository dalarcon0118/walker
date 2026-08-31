"""application/services/trace_service.py — TraceService.

Application Service (fachada pública) de event_tracing (ADR-WALKER-048).

Orquesta el Use Case de búsqueda (`use_cases/search_traces.py`), que es
implementación interna del módulo (INV-013). Consumidores externos usan
ESTE servicio, nunca el Use Case directo. No contiene lógica de negocio:
solo delega y traduce formatos.
"""
from __future__ import annotations

from typing import Any


class TraceService:
    """Fachada pública del TraceGraph: carga de eventos y búsqueda."""

    @staticmethod
    def search(events: list[Any], filters: dict | None = None) -> list[dict]:
        """Busca eventos que coincidan con los filtros (fail-closed)."""
        from ..use_cases.search_traces import SearchTracesUseCase

        return SearchTracesUseCase.execute(events, filters or {})

    @staticmethod
    def load_and_search(chain_dir: str, filters: dict | None = None) -> list[dict]:
        """Carga los traces del chain_dir y aplica la búsqueda (fail-closed)."""
        from features.event_tracing.infrastructure.repository import load_traces

        events = load_traces(chain_dir)
        return TraceService.search(events, filters or {})
