"""application/ports/trace_repository.py — Interfaz del repositorio Trace.

Puerto de salida (driven port) en Clean Architecture (ADR-WALKER-018).
Define el contrato que la capa de infraestructura implementa. La capa de
aplicacion depende de esta abstraccion, no de CognitiveGraph directamente.

Contrato de unicidad: el repositorio garantiza que load_traces() nunca
devuelve eventos duplicados (misma firma timestamp/event_type/tool/data).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from features.event_tracing.domain.models import TraceEvent


class TraceRepository(ABC):
    """Contrato de persistencia/lectura del TraceGraph (indice trace)."""

    @abstractmethod
    def load_traces(self, chain_dir: str) -> list[TraceEvent]:
        """Carga todos los eventos de un sprint (canonical, deduped)."""
        ...

    @abstractmethod
    def push_event(self, chain_dir: str, data: dict) -> dict:
        """Almacena un evento en el indice trace (canonical).

        Retorna {"ok": bool, "data": {"event_id", "event_type"}}.
        """
        ...
