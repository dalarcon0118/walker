"""knowledge_query task — consulta unificada del conocimiento del proyecto.

Concepto de negocio: consultar la base de conocimiento (CKG) para buscar,
guardar, activar, reemplazar o extraer contexto de conocimiento. Unifica
los sub-comandos legacy (ckg_analyze con action=search/save/active/
supersede/context) bajo un nombre de operación de negocio.

Result pattern (dry-python/returns): los cores con @safe nunca lanzan; el
wrapper decide con is_successful y hace fallback explícito + log
(convention-001: nunca except silencioso).
"""
from __future__ import annotations

import json
import logging
from typing import Any
from returns.pipeline import is_successful
from returns.result import safe
from shared.common.shared_domain import ExecutionContext, TaskResult

logger = logging.getLogger(__name__)


@safe
def _resolve_default_port(chain_dir: str) -> Any:
    """Core puro: resuelve el adaptador CKG (puede lanzar)."""
    from features.cognitive_knowledge.application.services.ckg_service import (
        CkgApplicationService,
    )
    adapter = CkgApplicationService.get_analysis_adapter(chain_dir)
    if adapter is None:
        raise RuntimeError(
            f"no se pudo construir ckg_analysis_adapter({chain_dir!r})"
        )
    return adapter


@safe
def _dispatch_fallback(action: str, data: dict, chain_dir: str) -> dict:
    """Core puro: despacho sin port usando el Application Service del CKG
    (puede lanzar). ADR-WALKER-048: nunca Use Cases directos (INV-013)."""
    from features.cognitive_knowledge.application.services.ckg_service import (
        CkgApplicationService,
    )
    from features.project import ProjectService

    paths = ProjectService.resolve_paths(chain_dir)
    project_root = str(paths.project_root)

    result: dict[str, Any] = {"action": action}
    if action == "search":
        query = data.get("query", data.get("text", ""))
        ckg_result = CkgApplicationService.gather_context(chain_dir, query=query)
        result["result"] = (
            ckg_result.value_or([]) if is_successful(ckg_result) else []
        )
    elif action == "save":
        nodes = data.get("nodes", [])
        task_id = data.get("task_id", "")
        ckg_result = CkgApplicationService.save_nodes(chain_dir, nodes, task_id=task_id)
        result["result"] = (
            ckg_result.value_or({})
            if is_successful(ckg_result)
            else {"error": str(ckg_result.failure())}
        )
    elif action == "active":
        type_name = data.get("type", "")
        result["result"] = CkgApplicationService.query_active(
            chain_dir, type_name=type_name
        )
    elif action == "context":
        files = data.get("files", [])
        result["result"] = CkgApplicationService.query_by_files(chain_dir, files)
    else:
        query = data.get("query", action)
        ckg_result = CkgApplicationService.gather_context(chain_dir, query=query)
        result["result"] = (
            ckg_result.value_or([]) if is_successful(ckg_result) else []
        )
    return result


def _execute_with_port(port: Any, action: str, data: dict, result: dict) -> TaskResult:
    if action == "search":
        query = data.get("query", data.get("text", ""))
        result["result"] = port.search_knowledge(query, data.get("max_results", 20))
    elif action == "save":
        nodes = data.get("nodes", [])
        project_id = data.get("_project_id", "")
        result["result"] = port.save_knowledge(nodes, project_id)
    elif action == "active":
        result["result"] = port.get_active_knowledge(data.get("type", ""))
    elif action == "supersede":
        result["result"] = port.supersede_decision(
            data.get("old_id", ""), data.get("new_id", ""), data.get("statement", "")
        )
    elif action == "context":
        result["result"] = port.get_knowledge_for_files(data.get("files", []))
    else:
        result["result"] = port.search_knowledge(action)
    result.setdefault("next_step", "Use 'action' to refine")
    return TaskResult.success(data=result)


def knowledge_query(
    ctx: ExecutionContext,
    data: dict[str, Any],
    port: Any | None = None,
) -> TaskResult:
    """Task canónica: consulta el conocimiento del proyecto."""
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure(
            "chain_dir not available",
            data={"next_step": "Run 'walker prepare <path>' first"},
        )

    action = data.get("action", "search")

    if port is not None:
        return _execute_with_port(port, action, data, {"action": action})

    resolved = _resolve_default_port(chain_dir)
    if is_successful(resolved) and resolved.value_or(None) is not None:
        return _execute_with_port(
            resolved.value_or(None), action, data, {"action": action}
        )

    dispatched = _dispatch_fallback(action, data, chain_dir)
    if is_successful(dispatched):
        result = dispatched.value_or({"action": action})
        result.setdefault(
            "next_step",
            "Use 'action' to refine: search, save, active, supersede, context",
        )
        return TaskResult.success(data=result)

    error = dispatched.failure()
    logger.warning(
        "knowledge_query action '%s' fallo (%s: %s)",
        action,
        type(error).__name__,
        error,
    )
    return TaskResult.failure(f"knowledge_query failed: {error}", data={"action": action})


def register(registry: Any) -> None:
    registry.register(
        "knowledge_query",
        knowledge_query,
        metadata={
            "description": "Consulta el conocimiento del proyecto: buscar, guardar, activar, reemplazar o extraer contexto.",
            "usage": 'walker <project>/knowledge_query \'{"action":"search","query":"auth"}\'',
            "example": 'walker <project>/knowledge_query \'{"action":"active","type":"decision"}\'',
            "tags": ["knowledge", "query", "unified"],
            "scope": "public",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Sub-comando: search, save, active, supersede, context (default search)", "example": "search"},
                    "query": {"type": "string", "description": "Texto de búsqueda full-text (action=search)", "example": "authentication"},
                    "text": {"type": "string", "description": "Alias de query (action=search)", "example": "auth"},
                    "max_results": {"type": "integer", "description": "Límite de resultados del search (default 20)", "example": 20},
                    "nodes": {"type": "array", "description": "Nodos de conocimiento a guardar (action=save)", "example": [{"type": "decision", "statement": "..."}]},
                    "type": {"type": "string", "description": "Filtro de tipo (action=active)", "example": "decision"},
                    "files": {"type": "array", "description": "Archivos para extraer contexto (action=context)", "example": ["src/auth.py"]},
                    "old_id": {"type": "string", "description": "ID de decisión a reemplazar (action=supersede)", "example": "dec-123"},
                    "new_id": {"type": "string", "description": "ID de la nueva decisión (action=supersede)", "example": "dec-456"},
                    "statement": {"type": "string", "description": "Texto/motivo de la decisión (action=supersede)", "example": "Deprecado por nueva arquitectura"},
                },
                "required": [],
            },
        },
    )
