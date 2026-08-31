"""features/cognitive_knowledge/module.py — Manifiesto CKG (ADR-066 F2).

Expone los servicios de grafo de conocimiento (CKG). No implementa puertos del
motor; es un bounded context de features con su propio composition root
(ckg_service / ckg_repository / ckg_analysis_adapter).
"""
from __future__ import annotations

from features.cognitive_knowledge import CodeKnowledgeGraph, CkgRepository
from shared.libs.module_system.contracts import ModuleManifest


MODULE = ModuleManifest(
    name="cognitive_knowledge",
    imports=["shared"],
    providers=[CodeKnowledgeGraph, CkgRepository],
    adapters=[],
    exports=["ckg_service", "ckg_repository", "ckg_analysis_adapter"],
)

# Auto-registro por import (ADR-WALKER-068): el composition root importa este
# modulo; al importarlo se registra su manifest. Sin lista central de paths.
from shared.libs.module_system.global_registry import register_manifest as _register_manifest

_register_manifest(MODULE)

# ADR-068 Fase 2: importar el schema de la feature dispara el auto-registro
# del indice de memoria (register_memory_index).
from features.cognitive_knowledge import schema as _schema  # noqa: F401
