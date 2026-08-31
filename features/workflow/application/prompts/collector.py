"""collector.py — Recolecta datos para inyectar en templates."""

from __future__ import annotations

import logging
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)


def _build_cg(chain_dir: str, searcher_factory=None) -> Any:
    """Construye el searcher vía DI para el chain_dir.

    Fase 5 (M-04): resuelve paths vía el puerto IProjectContext (no importa
    ProjectService concreto) + searcher CANÓNICO de app.memories (C7).
    """
    from shared.core.engine.domain.ports_registry import resolve_port
    from shared.core.engine.domain.ports import IProjectContext

    port = resolve_port(IProjectContext)
    paths = port.resolve_paths(chain_dir) if port is not None else None
    if paths is None:
        return None
    if searcher_factory is not None:
        return searcher_factory(paths.project_root)
    from app.memories.registration import build_searcher

    return build_searcher(paths.project_root)


def collect_activity(chain_dir: str, node_id: str | None = None) -> dict:
    cg = _build_cg(chain_dir)
    activity = {"id": node_id or "unknown", "description": ""}
    if node_id:
        r = cg.search("ckg", {"tag": node_id.lower()})
        if r["hits"]["total"] > 0:
            activity["description"] = r["hits"]["hits"][0]["_source"].get(
                "statement", ""
            )
    return activity


def collect_project_info(chain_dir: str) -> dict:
    cg = _build_cg(chain_dir)
    info = {"language": "", "architecture": "", "patterns": [], "quality_gates": []}
    r = cg.search("ckg", {"tag": "architecture"})
    if r["hits"]["total"] > 0:
        info["architecture"] = r["hits"]["hits"][0]["_source"].get("statement", "")
    r = cg.search("ckg", {"type": "pattern"})
    info["patterns"] = [
        h["_source"].get("statement", "") for h in r["hits"]["hits"][:5]
    ]
    r = cg.search("ckg", {"type": "quality"})
    info["quality_gates"] = [
        h["_source"].get("statement", "") for h in r["hits"]["hits"][:5]
    ]
    return info


def collect_available_tasks(chain_dir: str, node_id: str | None = None) -> list[dict]:
    # M-06 (Fase 5): resolver el task_registry del CONTENEDOR DI global
    # (module_registry, by id+contrato) — sin importar AppContainer (C7).
    from shared.libs.module_system.registry import module_registry

    registry = module_registry.get_service("task_registry")
    tasks = registry.list_tasks_with_meta()
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


def collect_knowledge(chain_dir: str, context: dict | None = None) -> dict:
    cg = _build_cg(chain_dir)
    ctx = context or {}
    global_knowledge = cg.search("ckg", {"tag": "global"})
    task_type = ctx.get("task_type", ctx.get("node_id", ""))
    relevant = (
        cg.search("ckg", {"tag": task_type}) if task_type else {"hits": {"hits": []}}
    )
    architecture = cg.search("ckg", {"tag": "architecture"})
    return {
        "global": [h["_source"] for h in global_knowledge["hits"]["hits"]],
        "relevant": [h["_source"] for h in relevant["hits"]["hits"]],
        "architecture": [h["_source"] for h in architecture["hits"]["hits"]],
    }


def collect_state(chain_dir: str) -> dict:
    """Estado del contexto: workflow (motor) + sprint (delta, fuente unica).

    Result pattern (convention-001): cada fuente degrada con log observable
    — debug para el workflow (la ausencia de .workflow-state.json es
    rutinaria en flujos sin motor) y warning para el delta (fallo real).
    """
    # Node/history: workflow (motor). Sprint status: delta (fuente unica).
    node, sub_state, history = "", "", []
    result = _load_workflow_state_core(chain_dir)
    if is_successful(result):
        wf = result.value_or(None)
        if wf:
            node = wf.current_node
            sub_state = wf.current_sub_state
            history = wf.history[-5:] or []
    else:
        logger.debug(
            "workflow state no disponible (%s); node/sub_state vacios",
            result.failure(),
        )
    sprint_id, status, task_id = "", "", ""
    result = _load_spring_state_core(chain_dir)
    if is_successful(result):
        sprint_id, status, task_id = result.value_or(("", "", ""))
    else:
        logger.warning(
            "estado spring no disponible (%s); contexto de sprint vacio",
            result.failure(),
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
    chain_dir: str, node_id: str | None = None, context: dict | None = None
) -> dict:
    ctx = context or {}
    state = collect_state(chain_dir)
    activity = collect_activity(chain_dir, node_id)
    data = {
        "activity": activity,
        "project": collect_project_info(chain_dir),
        "tasks": collect_available_tasks(chain_dir, node_id),
        "knowledge": collect_knowledge(chain_dir, context),
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
def _load_workflow_state_core(chain_dir: str) -> Any | None:
    """Core puro: estado del workflow (.workflow-state.json, puede lanzar).

    Consume el puerto IWorkflowStore (composition root), no el repo concreto.
    """
    from shared.core.engine.domain.ports_registry import resolve_port
    from shared.core.engine.domain.ports import IWorkflowStore

    store = resolve_port(IWorkflowStore)
    if store is None:
        return None
    return store.load_state(chain_dir)


@safe
def _load_spring_state_core(chain_dir: str) -> tuple[str, str, str]:
    """Core puro: (sprint_id, status, task_id) desde el DELTA (puede lanzar).

    Fuente unica del spring (ADR-WALKER-013): resolve → ensure import →
    spring + spec actual. Devuelve ("", "", "") si el chain_dir no
    resuelve a un proyecto (caso esperable).
    """
    from shared.core.engine.domain.ports_registry import resolve_port
    from shared.core.engine.domain.ports import ISprintService

    port = resolve_port(ISprintService)
    if port is None:
        return ("", "", "")
    spring_svc = port.from_chain_dir(chain_dir)
    if not spring_svc:
        return "", "", ""
    spring_id = spring_svc.get_spring_id(chain_dir)
    spring_svc.ensure_spring_imported(chain_dir, spring_id)
    spring = spring_svc.get_spring(spring_id)
    # Semantica original: si el spring no existe (import fallido), los
    # campos quedan vacios (no se usa el basename del chain como fallback).
    sprint_id = spring.get("_id", spring_id) if spring else ""
    status = spring.get("status", "") if spring else ""
    task_id = ""
    cur = spring_svc.get_current_spec(spring_id)
    if cur:
        task_id = cur.get("spec_id", "")
    return sprint_id, status, task_id
