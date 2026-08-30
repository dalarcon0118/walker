"""features/event_tracing/module.py — Manifiesto TraceGraph (ADR-066 F2).

Expone los servicios de trazabilidad (search, trace_repository,
build_reflection_context). No implementa el puerto ITracer del motor en este
módulo; el binding de ITracer se resuelve en el composition root (app).
"""
from __future__ import annotations

from features.event_tracing import (
    search,
    trace_repository,
    build_reflection_context,
)
from shared.libs.module_system.contracts import ModuleManifest


MODULE = ModuleManifest(
    name="event_tracing",
    imports=["shared"],
    providers=[],
    adapters=[],
    exports=["search", "trace_repository", "build_reflection_context"],
)

# Auto-registro por import (ADR-WALKER-068): el composition root importa este
# modulo; al importarlo se registra su manifest. Sin lista central de paths.
from shared.libs.module_system.global_registry import register_manifest as _register_manifest

_register_manifest(MODULE)

# ADR-068 Fase 2: importar el schema de la feature dispara el auto-registro
# del indice de memoria (register_memory_index).
from features.event_tracing import schema as _schema  # noqa: F401
