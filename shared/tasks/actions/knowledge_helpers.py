"""task_execution/tasks/kg_helpers.py — Helpers y lógica de tasks KG.

Aloja las funciones de query y write de KG para mantener kg.py como
thin adapter (< 200 líneas) que solo registra las tasks.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from shared.core.engine.domain.execution import ExecutionContext, TaskResult
from shared.tasks.infrastructure.kg_context import (
    _resolve_searcher,
    _resolve_project_root,
    _trace_kg_query,
)

logger = logging.getLogger(__name__)


def _ckg() -> Any:
    """Lazy (C3): resoluciona CkgApplicationService sin edge import-time."""
    from features.cognitive_knowledge.application.services.ckg_service import (
        CkgApplicationService,
    )

    return CkgApplicationService


def memory_schema(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Esquema de memorias disponibles."""
    project_id = data.get("_project_id", "")
    cg = _resolve_searcher(ctx.chain_dir or "", project_id)
    if cg is None:
        return TaskResult.failure(
            "No project context",
            data={
                "error": "No active sprint and no project context",
                "next_step": "Run 'walker <project>/status' or 'walker <project>/initialize_knowledge_base' first",
            },
        )
    payload = data.get("_payload", None)
    if payload and isinstance(payload, dict):
        index = payload.get("index", "")
        if index:
            result = _ckg().schema(cg, index)
            if result.get("type") == "error":
                return TaskResult.failure(result["message"])
            return TaskResult.success(data=result)
    index = data.get("index", "")
    if index:
        result = _ckg().schema(cg, index)
        if result.get("type") == "error":
            return TaskResult.failure(result["message"])
        return TaskResult.success(data=result)
    return TaskResult.success(
        data={
            "type": "memory_schema",
            "indexes": cg.list_indexes(),
            "help": _kg_help("memory_schema"),
        }
    )


def recall_knowledge(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Búsqueda en un índice de CognitiveGraph (fulltext o estructurado)."""
    project_id = data.get("_project_id", "")
    cg = _resolve_searcher(ctx.chain_dir or "", project_id)
    if cg is None:
        return TaskResult.failure(
            "No project context",
            data={
                "error": "No active sprint and no project context",
                "next_step": "Run 'walker <project>/status' or 'walker <project>/initialize_knowledge_base' first",
            },
        )
    payload = data.get("_payload", data)
    if isinstance(payload, str):
        payload = {"text": payload}
    if isinstance(payload, dict):
        payload.pop("action", None)
        payload.pop("_project_id", None)
        if "query" in payload and "text" not in payload:
            payload["text"] = payload.pop("query")
        if "index" in payload and "_index" not in payload:
            payload["_index"] = payload.pop("index")
    result = _ckg().search(cg, payload, project_id)
    if isinstance(result, dict) and result.get("type") == "error":
        return TaskResult.failure(result["message"])
    _trace_kg_query(ctx.chain_dir or "", payload.get("_index", "ckg"), payload, ctx)
    return TaskResult.success(data=result)


def search_memories(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Query federada multi-índice (delta, ckg, cpg, trace)."""
    project_id = data.get("_project_id", "")
    cg = _resolve_searcher(ctx.chain_dir or "", project_id)
    if cg is None:
        return TaskResult.failure(
            "No project context",
            data={
                "error": "No active sprint and no project context",
                "next_step": "Run 'walker <project>/status' or 'walker <project>/initialize_knowledge_base' first",
            },
        )
    payload = data.get("_payload", None)
    if payload and isinstance(payload, dict):
        result = _ckg().search_many(cg, payload)
        _trace_kg_query(ctx.chain_dir or "", "multi", payload, ctx)
        return TaskResult.success(data=result)
    queries_raw = data.get("queries", "")
    if not queries_raw:
        return TaskResult.success(data=_kg_help("search_memories"))
    try:
        queries = (
            json.loads(queries_raw) if isinstance(queries_raw, str) else queries_raw
        )
    except json.JSONDecodeError:
        return TaskResult.failure(f"Invalid queries JSON: {queries_raw}")
    result = _ckg().search_many(cg, queries)
    _trace_kg_query(ctx.chain_dir or "", "multi", queries, ctx)
    return TaskResult.success(data=result)


def documenting_knowledge(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    # ADR-WALKER-053: resolver repo path con precedencia explícito > ambiental
    from shared.tasks.application.project_context import (
        resolve_repo_path,
    )

    chain_dir = resolve_repo_path(data, ctx)
    project_id = (
        data.get("_project_id") or data.get("project_id") or ""
    )
    payload = data.get("_payload", data)
    nodes = payload.get("nodes")
    if isinstance(nodes, list) and nodes:
        if not chain_dir:
            return TaskResult.failure(
                "no project context",
                data={"next_step": "Pasá {project_id} o corré dentro del proyecto"},
            )
        from returns.pipeline import is_successful

        result = _ckg().save_nodes(
            chain_dir,
            nodes,
            task_id=ctx.task_id or "",
            session_id=ctx.session_id or "",
        )
        if not is_successful(result):
            return TaskResult.failure(str(result.failure()))
        res = result.value_or({})
        return TaskResult.success(
            data={
                "ok": bool(res.get("ok", res.get("saved", False))),
                "nodes_saved": res.get("nodes_saved", 0),
                "saved": bool(res.get("ok", res.get("saved", False))),
                **res,
            }
        )

    project_root = _resolve_project_root(chain_dir, project_id)
    if not project_root:
        return TaskResult.failure(
            "No project context",
            data={
                "error": "No active sprint and no project context available",
                "next_step": "Run 'walker <project>/status' or 'walker <project>/initialize_knowledge_base' first",
            },
        )

    try:
        res = _ckg().save_knowledge(
            chain_dir,
            project_root,
            payload,
            task_id=ctx.task_id or "",
            session_id=ctx.session_id or "",
        )
        if not res.get("ok"):
            return TaskResult.failure(
                res.get("error", res.get("message", "CKG write failed")),
                data={
                    "error": res.get("error", res.get("message", "CKG write failed")),
                    "next_step": "Run 'walker <project>/initialize_knowledge_base' to initialize CKG first",
                },
            )
        return TaskResult.success(data=res)
    except Exception as e:
        return TaskResult.failure(
            str(e),
            data={
                "error": f"CKG write failed: {e}",
                "next_step": "Run 'walker <project>/initialize_knowledge_base' to initialize CKG first",
            },
        )


def kg_agent_context(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    from shared.tasks.actions.knowledge_agent_context import kg_agent_context as _kg_agent_context
    return _kg_agent_context(ctx, data)


def _kg_help(topic: str = "") -> dict:
    """Retorna ayuda sobre las memorias de CognitiveGraph."""
    # Resolución DINÁMICA (ADR-WALKER-053): metadata importa este módulo a
    # nivel top; un ImportFrom aquí crearía arista estática mutua que el
    # gate DAG (ADR-052 D1) rechaza. import_module = forwardRef-style.
    import importlib

    _meta = importlib.import_module(
        "tasks.actions.knowledge_metadata"
    )
    _KG_HELP = _meta._KG_HELP

    return {
        "type": "kg_help",
        "topic": topic,
        "protocol": _KG_HELP["protocol"],
        "indices": _KG_HELP["indices"],
        "ejemplos": _KG_HELP["ejemplos"],
    }
