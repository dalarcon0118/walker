"""ckg_analyze task — API pública unificada del CKG.

Sub-comandos soportados:
  walker <project>/ckg_analyze '{"action":"search","query":"auth"}'
  walker <project>/ckg_analyze '{"action":"save","nodes":[...]}'
  walker <project>/ckg_analyze '{"action":"active","type":"decision"}'
  walker <project>/ckg_analyze '{"action":"supersede","old_id":"...","new_id":"..."}'
  walker <project>/ckg_analyze '{"action":"context","files":["src/auth.py"]}'

Retorna siempre el mismo envelope: {ok, action, result, next_step}

Result pattern (dry-python/returns): los cores con @safe nunca lanzan; el
wrapper decide con is_successful y hace fallback explícito con log
(convention-001: nunca except silencioso).
"""
from __future__ import annotations
import json
import logging
from typing import Any
from returns.pipeline import is_successful
from returns.result import safe
from libs.shared_domain import ExecutionContext, TaskResult

logger = logging.getLogger(__name__)


@safe
def _resolve_default_port(chain_dir: str) -> Any:
    """Core puro: resuelve el adaptador CKG (puede lanzar)."""
    from features.cognitive_knowledge.infrastructure.adapters.walker_ckg_adapter import WalkerCkgAdapter
    from features.cognitive_knowledge.application.ports.ckg_repository import CkgRepository
    from features.cognitive_knowledge.infrastructure.cognitive_ckg_repo import CognitiveCkgRepository
    from features.project import ProjectService

    paths = ProjectService.resolve_paths(chain_dir)
    data_dir = str(paths.project_root)
    repo = CognitiveCkgRepository(data_dir)
    return WalkerCkgAdapter(repo)


@safe
def _dispatch_fallback(action: str, data: dict, chain_dir: str) -> dict:
    """Core puro: despacho sin port usando los use cases del CKG (puede lanzar)."""
    from features.cognitive_knowledge.application.use_cases import (
        CheckCkgHealthUseCase,
        SaveToCkgUseCase,
        GatherCkgContextUseCase,
    )
    from features.project import ProjectService

    paths = ProjectService.resolve_paths(chain_dir)
    project_root = str(paths.project_root)

    result: dict[str, Any] = {"action": action}
    if action == "search":
        query = data.get("query", data.get("text", ""))
        ckg_result = GatherCkgContextUseCase.execute(chain_dir, query=query)
        result["result"] = ckg_result.value_or([]) if is_successful(ckg_result) else []
    elif action == "save":
        nodes = data.get("nodes", [])
        task_id = data.get("_project_id", "")
        ckg_result = SaveToCkgUseCase.execute(chain_dir, nodes, task_id=task_id)
        result["result"] = ckg_result.value_or({}) if is_successful(ckg_result) else {"error": str(ckg_result.failure())}
    elif action == "active":
        from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph
        ckg = CodeKnowledgeGraph(project_root)
        type_name = data.get("type", "")
        result["result"] = ckg.query({"status": "active", "type": type_name} if type_name else {"status": "active"})
    elif action == "context":
        files = data.get("files", [])
        from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph
        ckg = CodeKnowledgeGraph(project_root)
        all_nodes = ckg.query({})
        relevant = []
        for doc in all_nodes:
            doc_text = json.dumps(doc).lower()
            for tf in files:
                tf_lower = tf.lower()
                if tf_lower in doc_text:
                    relevant.append(doc)
                    break
        result["result"] = relevant[-5:]
    else:
        query = data.get("query", action)
        ckg_result = GatherCkgContextUseCase.execute(chain_dir, query=query)
        result["result"] = ckg_result.value_or([]) if is_successful(ckg_result) else []
    return result


def ckg_analyze(
    ctx: ExecutionContext,
    data: dict[str, Any],
    port: Any | None = None,
) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available", data={"next_step": "Run 'walker prepare <path>' first"})

    action = data.get("action", "search")

    # 1. Si hay port, despachar directo (sin try/except: el port delega en repos).
    if port is not None:
        return _execute_with_port(port, action, data, {"action": action})

    # 2. Sin port: resolverlo con Result pattern (fallback explícito + log).
    resolved = _resolve_default_port(chain_dir)
    if is_successful(resolved) and resolved.value_or(None) is not None:
        return _execute_with_port(resolved.value_or(None), action, data, {"action": action})

    # 3. Sin port resuelto: fallback a use cases con @safe (nunca lanza).
    dispatched = _dispatch_fallback(action, data, chain_dir)
    if is_successful(dispatched):
        result = dispatched.value_or({"action": action})
        result.setdefault("next_step", "Use 'action' to refine: search, save, active, supersede, context")
        return TaskResult.success(data=result)

    error = dispatched.failure()
    logger.warning(
        "ckg_analyze action '%s' fallo (%s: %s)",
        action, type(error).__name__, error,
    )
    return TaskResult.failure(f"ckg_analyze failed: {error}", data={"action": action})


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


def register(registry: Any) -> None:
    registry.register(
        "ckg_analyze",
        ckg_analyze,
        metadata={
            "description": "Unified CKG analysis: search, save, active, supersede, context",
            "usage": 'walker <project>/ckg_analyze \'{"action":"search","query":"auth"}\'',
            "example": 'walker <project>/ckg_analyze \'{"action":"active","type":"decision"}\'',
            "tags": ["ckg", "unified", "analysis"],
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
