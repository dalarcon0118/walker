"""application/task_dispatcher.py — execute_public_task: neutral task executor.

SSOT for "execute a public task by name" (bridge MCP/CLI).

Original: 77 LOC with ScopeGuard + resolve_service.
Simplified: 40 LOC with injected registry, no ScopeGuard (tasks deferred to Sprint 5).
"""
from __future__ import annotations

from typing import Any

from ..domain.execution import ExecutionContext


def execute_public_task(
    name: str,
    project_id: str,
    chain_dir: str,
    task_data: dict,
    session_id: str = "mcp",
    channel: str = "mcp",
    registry: Any = None,
    workflow_context: Any = None,
    cpg_adapter: Any = None,
    sprint_service: Any = None,
) -> dict:
    """Execute a public task by name.

    Pipeline:
        1. Resolve registry: explicit param > workflow_context.task_registry > error
        2. Build ExecutionContext
        3. Merge _payload into task_data
        4. Execute via registry
        5. Translate Result → dict

    Never raises: returns {ok: True, data} or {ok: False, error}.
    """
    # Resolve registry: explicit > context > error
    if registry is None and workflow_context is not None:
        registry = getattr(workflow_context, "task_registry", None)
    if registry is None:
        return {
            "ok": False,
            "error": "Task registry not available",
            "name": name,
        }

    ctx = ExecutionContext.create(
        session_id=session_id,
        chain_dir=chain_dir,
        channel=channel,
        cpg_adapter=cpg_adapter,
        project_id=project_id,
        sprint_service=sprint_service,
    )

    data = dict(task_data or {})
    if project_id:
        # El project_id de la spec manda (el CLI lo quita del payload para que
        # no se pueda apuntar a otro proyecto). Los handlers lo leen de
        # `data["project_id"]` (session_bind, session_open), así que debe
        # estar ahí además de en `_project_id`.
        data["project_id"] = project_id
        data["_project_id"] = project_id
    else:
        data.setdefault("_project_id", project_id)

    # Merge _payload into top-level data
    payload = data.get("_payload", {})
    if isinstance(payload, dict):
        for k, v in payload.items():
            data.setdefault(k, v)

    try:
        result = registry.execute(name, ctx, data)
    except Exception as e:
        return {"ok": False, "error": str(e), "name": name}

    result_data = result.data or {}
    if not result.ok:
        result_data["error"] = result.error or result_data.get("error", "Task failed")
        return {"ok": False, "error": result_data["error"], "data": result_data}

    return {"ok": True, "data": result_data}
