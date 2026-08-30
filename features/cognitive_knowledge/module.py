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
