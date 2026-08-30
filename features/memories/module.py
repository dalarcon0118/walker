"""features/memories/module.py — Manifiesto de MemoryModule (ADR-WALKER-068).

UNA responsabilidad: semántica de las memorias del walker. Depende SOLO del
storage genérico (shared) — nunca de features (INV-W068-04).
Fase 1: declaración de boundaries; providers se pueblan en Fase 2 (move de
`app/memory_schemas.py`, `app/memories.py`, `app/context_format.py`).
"""
from __future__ import annotations

from shared.libs.module_system.contracts import ModuleManifest


MODULE = ModuleManifest(
    name="features_memories",
    imports=["shared"],
    providers=[],
    adapters=[],
    exports=["memory_query_service", "memory_schemas", "context_format"],
    register=None,
)