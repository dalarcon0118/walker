"""CKG side-effects: delegan en CkgApplicationService.

Result pattern (dry-python/returns): los cores con @safe nunca lanzan; los
wrappers degradan con fallback explícito + log (convention-001).
"""
from __future__ import annotations
import logging
from typing import Any
from returns.pipeline import is_successful
from returns.result import safe
from shared.common.shared_domain import ExecutionContext
from core.engine.domain.side_effect_contract import as_side_effect, SCOPE_KNOWLEDGE
from features.project import ProjectService

logger = logging.getLogger(__name__)


@safe
def _get_project_root_core(chain_dir: str) -> str:
    """Core puro: resuelve el project_root desde chain_dir (puede lanzar)."""
    paths = ProjectService.resolve_paths(chain_dir)
    return str(paths.project_root)


def _get_project_root(chain_dir: str) -> str:
    """Resolve project root from chain_dir (used by mark_indexed)."""
    result = _get_project_root_core(chain_dir)
    if is_successful(result):
        return result.value_or("")
    logger.warning(
        "ckg_persist: no se pudo resolver project_root (%s: %s)",
        type(result.failure()).__name__, result.failure(),
    )
    return ""


@safe
def _mark_indexed_core(project_root: str) -> None:
    """Core puro: marca CKG como up_to_date en el watcher (puede lanzar)."""
    from features.project import FileWatcher
    FileWatcher(project_root).mark_indexed("ckg")


@as_side_effect(scope=SCOPE_KNOWLEDGE)
def documenting_knowledge(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    if not ctx.chain_dir:
        return {"ok": False, "error": "chain_dir not available", "nodes_saved": 0}
    try:
        from features.cognitive_knowledge.application.services.ckg_service import CkgApplicationService

        nodes = params.get("nodes", [])
        if isinstance(nodes, list):
            for item in nodes:
                if isinstance(item, dict) and "label" in item:
                    item.setdefault("metadata", {})
                    item["metadata"].setdefault("task_id", ctx.task_id)
                    item["metadata"].setdefault("source", f"session:{ctx.session_id}")
            result = CkgApplicationService.save_nodes(ctx.chain_dir, nodes)
        pr = _get_project_root(ctx.chain_dir)
        if pr:
            marked = _mark_indexed_core(pr)
            if not is_successful(marked):
                logger.warning(
                    "ckg_persist: mark_indexed('ckg') fallo (%s: %s)",
                    type(marked.failure()).__name__, marked.failure(),
                )
        if isinstance(nodes, list):
            return {"ok": True, "nodes_saved": result.get("nodes_saved", 0), "result": result}
        return {"ok": True, "nodes_saved": 0}
    except Exception as e:
        logger.warning(
            "ckg_persist: documenting_knowledge fallo (%s: %s)", type(e).__name__, e,
        )
        return {"ok": False, "error": str(e), "nodes_saved": 0}


@as_side_effect(scope=SCOPE_KNOWLEDGE)
def delete_ckg_nodes(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Compensación canónica (plural): elimina nodos de conocimiento creados
    por documenting_knowledge durante el rollback de saga."""
    if not ctx.chain_dir:
        return {"ok": False, "error": "chain_dir not available", "deleted": False}
    try:
        from features.cognitive_knowledge.application.services.ckg_service import CkgApplicationService

        task_id = ctx.task_id or ""
        result = CkgApplicationService.delete_by_metadata(ctx.chain_dir, "task", task_id)
        return {"ok": True, "deleted": result.get("nodes_removed", 0) > 0, **result}
    except Exception as e:
        logger.warning(
            "ckg_persist: delete_ckg_nodes fallo (%s: %s)", type(e).__name__, e,
        )
        return {"ok": False, "error": str(e), "deleted": False}


def register(engine: Any) -> None:
    engine.register("documenting_knowledge", documenting_knowledge)
    engine.register("delete_ckg_nodes", delete_ckg_nodes)
