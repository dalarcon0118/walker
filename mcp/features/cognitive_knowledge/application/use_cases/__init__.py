"""use_cases/__init__.py — SHIM de compatibilidad (ADR-WALKER-048).

Los use cases del CKG viven en `use_cases/` (un archivo por clase, patrón
canonical). Este __init__ re-exporta las clases para backward compatibility
durante la migración incremental (ADR-WALKER-048 §6): `from
features.cognitive_knowledge.application.use_cases import X` sigue
funcionando.

Consumidores nuevos: usen los Application Services
(`features.cognitive_knowledge.application.services.ckg_service`), NUNCA
estos Use Cases directos (INV-013).
"""
from __future__ import annotations

from .check_ckg_health import CheckCkgHealthUseCase
from .gather_ckg_context import GatherCkgContextUseCase
from .save_to_ckg import SaveToCkgUseCase
from .collect_knowledge import CollectKnowledgeUseCase
from .get_ckg_nodes_for_task import GetCkgNodesForTaskUseCase
from .index_repo import IndexRepoUseCase
from .research_topic import ResearchTopicUseCase

__all__ = [
    "CheckCkgHealthUseCase",
    "GatherCkgContextUseCase",
    "SaveToCkgUseCase",
    "CollectKnowledgeUseCase",
    "GetCkgNodesForTaskUseCase",
    "IndexRepoUseCase",
    "ResearchTopicUseCase",
]
