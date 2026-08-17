"""CKG — Code Knowledge Graph (desarrollo de software).

Construido sobre _walker/shared/knowledge_graph.py (grafo multi-dominio).
Define tipos y operaciones específicas del dominio de desarrollo.

Composition root: ckg_service() centraliza la construccion del grafo
(patron espejo de delta_tracking.spring_service). Los consumidores
importan desde la RAIZ del paquete (from features.cognitive_knowledge
import ckg_service), NUNCA desde la ruta interna application/ckg. Si la
implementacion cambia de lugar o se reemplaza, solo se actualiza este
modulo — los consumidores no se tocan.
"""
from __future__ import annotations

import logging
from typing import Any

from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph
from features.cognitive_knowledge.application.ports.ckg_repository import CkgRepository
from features.cognitive_knowledge.domain.knowledge_graph import KnowledgeNode as Node, KnowledgeEdge as Edge, KnowledgeGraph as Graph
from features.cognitive_knowledge.domain.values import CkgNodeId, CkgEdgeId, SearchFilters, KnowledgeScope

logger = logging.getLogger(__name__)

__all__ = [
    "CodeKnowledgeGraph",
    "Node",
    "Edge",
    "Graph",
    "CkgRepository",
    "ckg_service",
    "ckg_repository",
    "ckg_analysis_adapter",
    "CkgNodeId",
    "CkgEdgeId",
    "SearchFilters",
    "KnowledgeScope",
]


def ckg_service(
    project_dir: str = "",
    repo: CkgRepository | None = None,
) -> CodeKnowledgeGraph | None:
    """Composition root del CKG: construye CodeKnowledgeGraph con su repo.

    Punto unico de construccion: los consumidores no deciden como resolver
    el grafo ni que repositorio usar. Si mañana cambia la firma del
    constructor o el repo por defecto, solo se toca esta funcion.

    Args:
        project_dir: Directorio cognitivo del proyecto (.cognitive/projects/...).
        repo: Repositorio inyectado (tests/alternativas) — por defecto
              CognitiveCkgRepository.

    Returns:
        CodeKnowledgeGraph construido, o None si no hay project_dir.
    """
    if not project_dir:
        return None
    return CodeKnowledgeGraph(project_dir, repo=repo)


def ckg_repository(project_dir: str = "") -> CkgRepository | None:
    """Factory del repo CKG (CognitiveCkgRepository por defecto).

    Punto unico de construccion del repositorio: los consumidores reciben
    el port CkgRepository y no importan infraestructura concreta. Devuelve
    None si no hay project_dir resolvible.
    """
    if not project_dir:
        return None
    from features.cognitive_knowledge.infrastructure.cognitive_ckg_repo import (
        CognitiveCkgRepository,
    )

    return CognitiveCkgRepository(project_dir)


def ckg_analysis_adapter(chain_dir: str = "") -> Any | None:
    """Factory del adapter de analisis CKG (ICkgServicePort default).

    Encapsula la composicion interna (CognitiveCkgRepository + WalkerCkgAdapter)
    para que las tasks (ej: task_execution/tasks/ckg_analyze) importen solo
    desde la raiz del paquete.

    Returns:
        WalkerCkgAdapter construido, o None si no hay contexto resolvible.
        Nunca lanza (convention-001: log ante fallo).
    """
    if not chain_dir:
        return None
    try:
        from features.cognitive_knowledge.infrastructure.adapters.walker_ckg_adapter import (
            WalkerCkgAdapter,
        )
        from features.project import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        repo = ckg_repository(str(paths.project_root))
        if repo is None:
            logger.warning(
                "cognitive_knowledge: ckg_analysis_adapter sin repo para %r",
                chain_dir,
            )
            return None
        return WalkerCkgAdapter(repo)
    except Exception as e:
        logger.warning(
            "cognitive_knowledge: no se pudo construir el adapter CKG (%s: %s)",
            type(e).__name__, e,
        )
        return None