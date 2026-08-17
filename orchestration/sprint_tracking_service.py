"""orchestration/sprint_tracking_service.py — SprintTrackingService.

Cross-feature orchestrator para index_sprint_tasks/rebuild_sprint_index (ADR-051 Fase 4 G10).

Orquesta 2 features: project (path resolution) × delta_tracking (SpringService).
El side-effect sprint_tracking.py se reduce a un thin adapter.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SprintTrackingService:
    """Indexa y re-indexa el sprint en CognitiveGraph.

    Orquesta ProjectService (paths) y SpringService (delta tracking).
    No contiene lógica de negocio propia — solo coordina.
    """

    @staticmethod
    def index_sprint_tasks(chain_dir: str, project_root: str = "") -> dict[str, Any]:
        """Indexa el sprint actual en el índice delta de CognitiveGraph."""
        if not chain_dir:
            return {"ok": False, "error": "chain_dir not available", "indexed_tasks": 0}
        try:
            from features.project import ProjectService

            paths = ProjectService.resolve_paths(chain_dir)
            cg = _build_graph(paths.project_root)
            from features.delta_tracking import SpringService

            spring_svc = SpringService.from_chain_dir(chain_dir)
            if not spring_svc:
                return {"ok": False, "error": "SpringService no disponible", "indexed_tasks": 0}
            count = spring_svc.index_sprint(chain_dir, cg)
            return {"ok": True, "indexed_tasks": count}
        except Exception as e:
            logger.warning(
                "sprint_tracking: index_sprint_tasks fallo (%s: %s)", type(e).__name__, e,
            )
            return {"ok": False, "error": str(e), "indexed_tasks": 0}

    @staticmethod
    def rebuild_sprint_index(chain_dir: str, project_root: str = "") -> dict[str, Any]:
        """Re-indexa todos los sprints en el índice delta de CognitiveGraph."""
        if not chain_dir:
            return {"ok": False, "error": "chain_dir not available", "reindexed_tasks": 0}
        try:
            from features.project import ProjectService

            paths = ProjectService.resolve_paths(chain_dir)
            cg = _build_graph(paths.project_root)
            from features.delta_tracking import SpringService

            spring_svc = SpringService.from_chain_dir(chain_dir)
            if not spring_svc:
                return {"ok": False, "error": "SpringService no disponible", "reindexed_tasks": 0}
            count = spring_svc.index_all(str(paths.project_root), cg)
            return {"ok": True, "reindexed_tasks": count}
        except Exception as e:
            logger.warning(
                "sprint_tracking: rebuild_sprint_index fallo (%s: %s)", type(e).__name__, e,
            )
            return {"ok": False, "error": str(e), "reindexed_tasks": 0}


def _build_graph(project_root: str) -> Any:
    """Resuelve el orquestador CognitiveGraph vía DI (key "cognitive_graph")."""
    from core.engine.bootstrap import get_module_service

    graph_factory = get_module_service("cognitive_graph")
    return graph_factory(project_root)
