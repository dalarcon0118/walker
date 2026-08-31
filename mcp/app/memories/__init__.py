"""app/memories — Módulo de aplicación de memorias (ADR-068 Fase 2).

Capa de aplicación del host: MemoryQueryService (facade) + aplicación de los
índices auto-registrados por las features (forFeature). NO es dueña de ningún
schema (INV-W068-08) — las features se auto-registran su register_<memoria>_index.
"""
from __future__ import annotations

from app.memories.registration import (
    COMPACT_FIELDS,
    SEVERITY_ORDER,
    apply_memory_indexes,
    build_cognitive_graph,
    build_searcher,
)
from app.memories.query import MemoryQueryService

__all__ = [
    "MemoryQueryService",
    "COMPACT_FIELDS",
    "SEVERITY_ORDER",
    "apply_memory_indexes",
    "build_cognitive_graph",
    "build_searcher",
]