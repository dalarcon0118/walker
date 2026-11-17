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

# ── Vocabulario de conocimiento (fix 2026-11-16) ───────────────────────────
# Los collectors pedían `type: "quality"` (no existe en el schema) y los tags
# "global"/"architecture", que NINGÚN proyecto usa: medido sobre las 7 memorias
# registradas, los types reales son decision/design-decision/implementation*/
# verification/fix y los tags son de dominio (win-win, ssot, adr-042...). El
# contexto que el workflow inyectaba en cada prompt llegaba vacío.
#
# Se consulta por `type` (vocabulario del schema, estable) y por `tag` (libre,
# específico del proyecto) y se UNEN los resultados sin duplicar.
KNOWLEDGE_QUERIES: dict[str, tuple[dict[str, str], ...]] = {
    "architecture": (
        {"type": "design-decision"},
        {"type": "decision"},
        {"type": "component"},
        {"tag": "architecture"},
    ),
    "pattern": (
        {"type": "pattern"},
        {"type": "component"},
        {"type": "design-decision"},
        {"tag": "pattern"},
    ),
    "quality": (
        {"type": "rule"},
        {"type": "convention"},
        {"type": "verification"},
        {"tag": "quality"},
    ),
    "global": (
        {"type": "convention"},
        {"type": "rule"},
        {"type": "decision"},
        {"tag": "global"},
    ),
}

# Nodo del workflow → tipo de conocimiento que lo alimenta.
TASK_TYPE_ALIASES: dict[str, str] = {
    "implement": "implementation",
    "verify": "verification",
    "analyze": "analysis",
}


def _search_hits(ckg_searcher: Any, query: dict[str, str]) -> list[dict]:
    """Hits de una consulta, tolerando las dos interfaces del searcher.

    `CodeKnowledgeGraph.search_many` devuelve {responses: {ckg: {hits...}}};
    `CodeKnowledgeGraph.search` devuelve una LISTA de docs; el searcher legacy
    (ES-like) devuelve {hits: {hits: [...]}}. El collector se escribió contra
    la tercera, así que con las otras dos lanzaba AttributeError y el contexto
    llegaba vacío (medido 2026-11-16).
    """
    if hasattr(ckg_searcher, "search_many"):
        out = ckg_searcher.search_many({"ckg": dict(query)}) or {}
        body = (out.get("responses") or {}).get("ckg") or {}
        return (body.get("hits") or {}).get("hits") or []
    result = ckg_searcher.search("ckg", dict(query))
    if isinstance(result, list):
        return [{"_source": doc} for doc in result]
    if isinstance(result, dict):
        return (result.get("hits") or {}).get("hits") or []
    return []


def _union_search(ckg_searcher: Any, queries: tuple[dict[str, str], ...]) -> list[dict]:
    """Une los hits de varias consultas, sin duplicar por `_id`.

    Cada consulta degrada por separado: un fallo en una no deja sin contexto a
    las demás (el collector es best-effort por contrato).
    """
    hits: list[dict] = []
    seen: set[str] = set()
    for query in queries:
        try:
            found = _search_hits(ckg_searcher, query)
        except Exception as e:  # noqa: BLE001 — best-effort, se loguea abajo
            logger.debug("_union_search(%s): %s", query, e)
            continue
        for hit in found:
            key = str(hit.get("_id") or "")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            hits.append(hit)
    return hits


def _statements(hits: list[dict], limit: int | None = None) -> list[str]:
    out = [
        str(h.get("_source", {}).get("statement", ""))
        for h in hits
        if h.get("_source", {}).get("statement")
    ]
    return out[:limit] if limit else out


def _sources(hits: list[dict]) -> list[dict]:
    """Mantiene el contrato histórico de `collect_knowledge` (lista de _source)."""
    return [h.get("_source", {}) for h in hits if h.get("_source")]


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
            hits = _search_hits(ckg_searcher, {"tag": node_id.lower()})
            if hits:
                activity["description"] = str(
                    hits[0].get("_source", {}).get("statement", "")
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
        architecture = _union_search(ckg_searcher, KNOWLEDGE_QUERIES["architecture"])
        if architecture:
            info["architecture"] = str(
                architecture[0].get("_source", {}).get("statement", "")
            )
        info["patterns"] = _statements(
            _union_search(ckg_searcher, KNOWLEDGE_QUERIES["pattern"]), limit=5
        )
        info["quality_gates"] = _statements(
            _union_search(ckg_searcher, KNOWLEDGE_QUERIES["quality"]), limit=5
        )
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
        task_type = str(ctx.get("task_type", ctx.get("node_id", "")) or "").lower()
        relevant_queries: tuple[dict[str, str], ...] = ()
        if task_type:
            alias = TASK_TYPE_ALIASES.get(task_type, task_type)
            relevant_queries = (
                {"tag": task_type},
                {"type": alias},
                {"type": f"{alias}_state"},
            )
        return {
            "global": _sources(
                _union_search(ckg_searcher, KNOWLEDGE_QUERIES["global"])
            ),
            "relevant": _sources(
                _union_search(ckg_searcher, relevant_queries) if relevant_queries else []
            ),
            "architecture": _sources(
                _union_search(ckg_searcher, KNOWLEDGE_QUERIES["architecture"])
            ),
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
