"""application/task_dispatcher.py — Ejecutor neutral de tasks publicas.

SSOT del pipeline "ejecutar una task publica por nombre" (bridge MCP).

ANTES (inversión de dependencia): las features internas (engine/cognitive/
code_analysis/delta_tracking/event_tracing) importaban _run_public_task DESDE
features.cli_interface.application.handlers — la capa de entrada dependía de
features internas (outer→inner invertido).

AHORA: este dispatcher vive en features.engine.application (la feature que
posee el task registry). Cualquier feature mcp.py o cli_interface lo usa.
Dirección correcta: inner feature expone su servicio; outer lo consume.
"""

from __future__ import annotations

from typing import Any


def dispatch_public_task(name: str, body: dict) -> dict:
    """Ejecuta una task publica del registry por nombre.

    Return-pattern + Result (returns): los fallos se traducen a un dict
    de error con status_code; nunca se lanza hacia el caller.

    Args:
        name: Nombre de la task publica.
        body: Body de datos (puede incluir _project_id / _payload).

    Returns:
        dict JSON-safe: {type, status_code, message, data} o {type:error}.
    """
    from returns.result import safe
    from returns.pipeline import is_successful

    from libs.shared_domain import ExecutionContext

    def _resolve_project_id() -> str:
        from features.project import ProjectService

        resolved = ProjectService.resolve_from_registry()
        if resolved:
            return resolved.get("project_id", "")
        return ""

    def _ensure_chain_dir(project_id: str) -> str:
        from features.project import ProjectService

        return ProjectService.find_active_sprint(project_id) or ""

    @safe
    def _exec() -> dict:
        from features.engine.bootstrap import bootstrap

        # 1. Resolver proyecto (desde body._project_id o registry global)
        project_id = (body or {}).get("_project_id", "") or ""
        if not project_id:
            project_id = _resolve_project_id()
        if not project_id:
            return {
                "type": "error",
                "status_code": 400,
                "message": "No project context — provide _project_id or run from project dir",
            }

        # 2. Resolver chain_dir (directorio del sprint del proyecto)
        chain_dir = _ensure_chain_dir(project_id)

        # 3. Ejecutar la task via el registry (mismo pipeline que handle_task)
        registry, _ = bootstrap()
        info = registry.get_task_info(name)
        if info and info.get("scope", "public") == "internal":
            return {
                "type": "error",
                "status_code": 400,
                "message": f"Task '{name}' es INTERNA del workflow y no se invoca directamente.",
            }

        ctx = ExecutionContext.create(session_id="mcp", chain_dir=chain_dir)
        task_data = dict(body or {})
        task_data.setdefault("_project_id", project_id)
        payload = task_data.get("_payload", {})
        if isinstance(payload, dict):
            for k, v in payload.items():
                task_data.setdefault(k, v)
        result = registry.execute(name, ctx, task_data)
        result_data = result.data or {}
        if not result.ok:
            result_data["error"] = result.error or result_data.get("error", "Task failed")
            return {
                "type": "error",
                "status_code": 400,
                "message": result_data["error"],
                "data": {"result": result_data},
            }
        return {
            "type": "task_result",
            "status_code": 200,
            "message": f"Task '{name}' executed successfully",
            "data": {"action": name, "ok": True, "result": result_data},
        }

    outcome = _exec()
    if is_successful(outcome):
        return outcome.value_or({})
    return {
        "type": "error",
        "status_code": 500,
        "message": f"Task '{name}' failed: {outcome.failure()}",
    }
