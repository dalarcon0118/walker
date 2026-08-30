"""app/orchestration/knowledge_persist_service.py — KnowledgePersistService.

Cross-feature orchestrator para documenting_knowledge/remove_knowledge (ADR-051 Fase 4 G10).

Orquesta 2 features: project (path resolution, watcher) × cognitive_knowledge (CKG).
El side-effect knowledge_persist.py se reduce a un thin adapter.
"""
from __future__ import annotations

import logging
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)


class KnowledgePersistService:
    """Persiste conocimiento en CKG y sincroniza el watcher.

    Orquesta ProjectService (paths, FileWatcher) y CkgApplicationService (CKG).
    No contiene lógica de negocio propia — solo coordina.
    """

    @staticmethod
    def documenting_knowledge(chain_dir: str, nodes: list[dict], task_id: str = "", session_id: str = "") -> dict[str, Any]:
        """Guarda nodos de conocimiento en CKG y marca el watcher como up_to_date."""
        if not chain_dir:
            return {"ok": False, "error": "chain_dir not available", "nodes_saved": 0}
        try:
            from features.project import ProjectService, FileWatcher
            from features.cognitive_knowledge.application.services.ckg_service import CkgApplicationService

            if isinstance(nodes, list):
                for item in nodes:
                    if isinstance(item, dict) and "label" in item:
                        item.setdefault("metadata", {})
                        item["metadata"].setdefault("task_id", task_id)
                        item["metadata"].setdefault("source", f"session:{session_id}")

            result = CkgApplicationService.save_nodes(chain_dir, nodes)

            paths = ProjectService.resolve_paths(chain_dir)
            pr = _resolve_project_root(paths)
            if pr:
                marked = _mark_indexed_core(pr)
                if not is_successful(marked):
                    logger.warning(
                        "knowledge_persist: mark_indexed('ckg') fallo (%s: %s)",
                        type(marked.failure()).__name__, marked.failure(),
                    )

            if isinstance(nodes, list):
                return {"ok": True, "nodes_saved": result.get("nodes_saved", 0), "result": result}
            return {"ok": True, "nodes_saved": 0}
        except Exception as e:
            logger.warning(
                "knowledge_persist: documenting_knowledge fallo (%s: %s)", type(e).__name__, e,
            )
            return {"ok": False, "error": str(e), "nodes_saved": 0}

    @staticmethod
    def remove_knowledge(chain_dir: str, task_id: str = "") -> dict[str, Any]:
        """Elimina nodos de conocimiento asociados a una tarea (rollback de saga)."""
        if not chain_dir:
            return {"ok": False, "error": "chain_dir not available", "removed": False}
        try:
            from features.cognitive_knowledge.application.services.ckg_service import CkgApplicationService

            result = CkgApplicationService.delete_by_metadata(chain_dir, "task", task_id)
            return {"ok": True, "removed": result.get("nodes_removed", 0) > 0, **result}
        except Exception as e:
            logger.warning(
                "knowledge_persist: remove_knowledge fallo (%s: %s)", type(e).__name__, e,
            )
            return {"ok": False, "error": str(e), "removed": False}


@safe
def _get_project_root_core(chain_dir: str) -> str:
    """Core puro: resuelve el project_root desde chain_dir (puede lanzar)."""
    from features.project import ProjectService

    paths = ProjectService.resolve_paths(chain_dir)
    return str(paths.project_root)


def _resolve_project_root(chain_dir: str) -> str:
    """Resolve project root from chain_dir (used by mark_indexed)."""
    result = _get_project_root_core(chain_dir)
    if is_successful(result):
        return result.value_or("")
    logger.warning(
        "knowledge_persist: no se pudo resolver project_root (%s: %s)",
        type(result.failure()).__name__, result.failure(),
    )
    return ""


@safe
def _mark_indexed_core(project_root: str) -> None:
    """Core puro: marca CKG como up_to_date en el watcher (puede lanzar)."""
    from features.project import FileWatcher

    FileWatcher(project_root).mark_indexed("ckg")
