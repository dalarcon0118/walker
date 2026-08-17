"""use_cases/__init__.py — Event tracing use cases (ADR-WALKER-048).

Los use cases del feature viven en `use_cases/` (un archivo por clase,
patrón canonical). Consumidores externos: usen los Application Services
(`features.event_tracing.application.services.trace_service`), NUNCA estos
Use Cases directos (INV-013).
"""
from __future__ import annotations

from .search_traces import SearchTracesUseCase, search_traces

__all__ = ["SearchTracesUseCase", "search_traces"]
