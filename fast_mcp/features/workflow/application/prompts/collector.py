"""collector.py — Recolecta datos para inyectar en templates.

Adaptado a fast_mcp desde el motor heredado (mcp/features/workflow/
application/prompts/collector.py, eliminado por commit 63efa79).

R1 CLEAN (no feature→feature imports): todas las dependencias externas
(CKG searcher, workflow store, task registry, sprint state loader) se
inyectan como parámetros opcionales. El composition root (WorkflowContext
o collector_factory) cablea las implementaciones concretas.

Cambios vs original:
  - `_build_cg` eliminado: el caller inyecta `ckg_searcher` directamente.
  - `collect_available_tasks` recibe `task_registry` como parámetro.
  - `collect_state` recibe `workflow_store` y `sprint_state_loader`.
  - Se eliminaron todas las dependencias de `shared.core.engine`, `app/`,
    y de otros features (cognitive_knowledge, delta_tracking).
  - Las dependencias de `shared_kernel.project_support` son permitidas
    (shared_kernel no es un feature).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)


def collect_activity(
    chain_dir: str,
    node_id: str | None = None,
    ckg_searcher: Any = None,
) -> dict:
    """Recupera actividad del nodo desde el CKG.

    Args:
        ckg_searcher: CognitiveGraphSearcher o similar con .search("ckg", query).
                      Inyectado por composition root (no resuelto internamente).
    """
    activity: dict[str, str] = {"id": node_id or "unknown", "description": ""}
    if node_id and ckg_searcher is not None:
        try:
            r = ckg_searcher.search("ckg", {"tag": node_id.lower()})
            if r.get("hits", {}).get("total", 0) > 0:
                activity["description"] = (
                    r["hits"]["hits"][0].get("_source", {}).get("statement", "")
                )
        except Exception as e:
            logger.debug("collect_activity(%s): %s", node_id, e)
    return activity


def collect_project_info(chain_dir: str, ckg_searcher: Any = None) -> dict:
    info: dict[str, Any] = {
        "language": "",
        "architecture": "",
        "patterns": [],
        "quality_gates": [],
    }
    if ckg_searcher is None:
        return info
    try:
        r = ckg_searcher.search("ckg", {"tag": "architecture"})
        if r.get("hits", {}).get("total", 0) > 0:
            info["architecture"] = (
                r["hits"]["hits"][0].get("_source", {}).get("statement", "")
            )
        r = ckg_searcher.search("ckg", {"type": "pattern"})
        info["patterns"] = [
            h.get("_source", {}).get("statement", "")
            for h in r.get("hits", {}).get("hits", [])[:5]
        ]
        r = ckg_searcher.search("ckg", {"type": "quality"})
        info["quality_gates"] = [
            h.get("_source", {}).get("statement", "")
            for h in r.get("hits", {}).get("hits", [])[:5]
        ]
    except Exception as e:
        logger.debug("collect_project_info: %s", e)
    return info


def collect_available_tasks(
    chain_dir: str,
    node_id: str | None = None,
    task_registry: Any = None,
) -> list[dict]:
    """Lista las tareas disponibles vía el task_registry inyectado.

    El task_registry es inyectado por el caller (composition root); no se
    resuelve por module_registry (que no existe en fast_mcp).
    """
    if task_registry is None:
        return []
    try:
        tasks = task_registry.list_tasks_with_meta()
    except Exception as e:
        logger.debug("collect_available_tasks: %s: %s", type(e).__name__, e)
        return []
    result = []
    for t in tasks:
        result.append(
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "usage": t.get("usage", f"walker <project>/{t['name']}"),
            }
        )
    return result


def collect_knowledge(
    chain_dir: str,
    context: dict | None = None,
    ckg_searcher: Any = None,
) -> dict:
    ctx = context or {}
    if ckg_searcher is None:
        return {"global": [], "relevant": [], "architecture": []}
    try:
        global_knowledge = ckg_searcher.search("ckg", {"tag": "global"})
        task_type = ctx.get("task_type", ctx.get("node_id", ""))
        relevant = (
            ckg_searcher.search("ckg", {"tag": task_type})
            if task_type
            else {"hits": {"hits": []}}
        )
        architecture = ckg_searcher.search("ckg", {"tag": "architecture"})
        return {
            "global": [
                h.get("_source", {})
                for h in global_knowledge.get("hits", {}).get("hits", [])
            ],
            "relevant": [
                h.get("_source", {})
                for h in relevant.get("hits", {}).get("hits", [])
            ],
            "architecture": [
                h.get("_source", {})
                for h in architecture.get("hits", {}).get("hits", [])
            ],
        }
    except Exception as e:
        logger.debug("collect_knowledge: %s", e)
        return {"global": [], "relevant": [], "architecture": []}


def collect_state(
    chain_dir: str,
    workflow_store: Any = None,
    sprint_state_loader: Callable[[str], tuple[str, str, str]] | None = None,
) -> dict:
    """Estado del contexto: workflow (motor) + sprint (delta).

    Result pattern (convention-001): cada fuente degrada con log observable.

    Args:
        workflow_store: puerto IWorkflowStore inyectado (no resuelto internamente).
        sprint_state_loader: callable(chain_dir) -> (sprint_id, status, task_id)
                             inyectado por composition root.
    """
    node, sub_state, history = "", "", []
    if workflow_store is not None:
        result = _load_workflow_state_core(workflow_store, chain_dir)
        if is_successful(result):
            wf = result.value_or(None)
            if wf:
                node = getattr(wf, "current_node", "")
                sub_state = getattr(wf, "current_sub_state", "")
                history = getattr(wf, "history", [])[-5:]
        else:
            logger.debug(
                "workflow state no disponible (%s); node/sub_state vacios",
                result.failure(),
            )

    sprint_id, status, task_id = "", "", ""
    if sprint_state_loader is not None:
        try:
            sprint_id, status, task_id = sprint_state_loader(chain_dir)
        except Exception as e:
            logger.debug(
                "sprint_state_loader failed (%s); contexto de sprint vacio", e
            )

    return {
        "node": node,
        "sub_state": sub_state,
        "task_id": task_id,
        "sprint_id": sprint_id,
        "status": status,
        "history": history,
    }


def collect_all(
    chain_dir: str,
    node_id: str | None = None,
    context: dict | None = None,
    task_registry: Any = None,
    workflow_store: Any = None,
    ckg_searcher: Any = None,
    sprint_state_loader: Callable[[str], tuple[str, str, str]] | None = None,
) -> dict:
    """Recolecta todo el contexto enriquecido para inyectar en templates.

    Todas las dependencias externas se inyectan (R1 CLEAN):
      - task_registry: ITaskRegistry → list_tasks_with_meta()
      - workflow_store: IWorkflowStore → load_state(chain_dir)
      - ckg_searcher: CognitiveGraphSearcher → search("ckg", query)
      - sprint_state_loader: callable(chain_dir) → (sprint_id, status, task_id)

    El composition root (WorkflowContext o un factory) las cablea antes de
    llamar a collect_all.
    """
    ctx = context or {}
    state = collect_state(
        chain_dir,
        workflow_store=workflow_store,
        sprint_state_loader=sprint_state_loader,
    )
    activity = collect_activity(chain_dir, node_id, ckg_searcher=ckg_searcher)
    data = {
        "activity": activity,
        "project": collect_project_info(chain_dir, ckg_searcher=ckg_searcher),
        "tasks": collect_available_tasks(chain_dir, node_id, task_registry=task_registry),
        "knowledge": collect_knowledge(
            chain_dir, context, ckg_searcher=ckg_searcher
        ),
        "state": state,
        "task_id": state.get("task_id", ctx.get("task_id", "")),
        "project_id": ctx.get("project_id", state.get("sprint_id", "")),
        "sprint_id": state.get("sprint_id", ""),
        "sprint_goal": ctx.get("sprint_goal", ""),
        "node": state.get("node", ""),
        "sub_state": state.get("sub_state", ""),
        "status": state.get("status", ""),
        "history": state.get("history", []),
        "session_id": ctx.get("session_id", ""),
        "chain_dir": chain_dir,
    }
    return data


# ── Cores @safe (Result pattern, convention-001) ─────────────────


@safe
def _load_workflow_state_core(workflow_store: Any, chain_dir: str) -> Any | None:
    """Core puro: carga el estado del workflow vía el store inyectado."""
    return workflow_store.load_state(chain_dir)
