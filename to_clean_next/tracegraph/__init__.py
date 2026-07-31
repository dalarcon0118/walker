"""scripts/tracegraph/ — SHIM de compatibilidad para tracegraph legacy.

MIGRATED TO: features/event_tracing/

Este shim redirige imports de scripts/tracegraph.XYZ a
features.event_tracing.XYZ via sys.modules forwarding.

Los modulos legacy (domain/models.py, application/search_service.py,
infrastructure/repository.py) se conservan como stubs pero su
contenido original se reemplazo por re-export desde la feature.

TASK-005: Migrar tracegraph/ y eventos a features/event-tracing/
"""
import sys
import importlib

# ── Mapeo de modulos legacy -> feature destino ──────────────────
_FORWARD = {
    "tracegraph.domain.models": "features.event_tracing.domain.models",
    "tracegraph.application.search_service": "features.event_tracing.application.search_service",
    "tracegraph.infrastructure.repository": "features.event_tracing.infrastructure.repository",
}

for legacy, target in _FORWARD.items():
    if legacy not in sys.modules:
        try:
            mod = importlib.import_module(target)
            sys.modules[legacy] = mod
        except ImportError:
            pass

# ── Exportar API publica ────────────────────────────────────────
from features.event_tracing import (  # noqa: E402
    search_traces,
    load_traces,
    TraceEvent,
    search,
)

__all__ = [
    "search_traces",
    "load_traces",
    "TraceEvent",
    "search",
]
