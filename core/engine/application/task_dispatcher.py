"""application/task_dispatcher.py — Ejecutor neutral de tasks publicas.

SSOT del pipeline "ejecutar una task publica por nombre" (bridge MCP).

ANTES (inversión de dependencia): las features internas (engine/cognitive/
code_analysis/delta_tracking/event_tracing) importaban _run_public_task DESDE
features.cli_interface.application.handlers — la capa de entrada dependía de
features internas (outer→inner invertido).

AHORA: este dispatcher vive en core.engine.application (la feature que
posee el task registry). Cualquier feature mcp.py o cli_interface lo usa.
Dirección correcta: inner feature expone su servicio; outer lo consume.
"""

from __future__ import annotations


def execute_public_task(
    name: str,
    project_id: str,
    chain_dir: str,
    task_data: dict,
    session_id: str = "cli",
    channel: str = "cli",
) -> dict:
    """Core compartido de dispatch CLI/MCP (SPEC-005 #5).

    Un solo lugar para: visibilidad SSOT (metadata.scope via SystemTaskRegistry),
    construcción de ExecutionContext, merge de _payload y ejecución con traducción
    de errores. Nunca lanza: devuelve {"ok": True, "data"} o {"ok": False, ...}.

    Nota de visibilidad: NO se bloquea una task inexistente (deja que
    registry.execute emita el error estándar), pero SÍ se bloquean las tasks
    scope=internal y scope=system desde CLI/MCP (channel cli/mcp). Las tasks
    system (p. ej. trace_push) solo las ejecuta el motor interno con
    channel='system' (ARQ objetivo SPEC-005; trace_event_publisher usa esa
    puerta para los eventos CPG→trace).
    """
    # resolve_service removed - using injected ports
    from core.engine.bootstrap import resolve_service
    from shared.common.shared_domain import ExecutionContext

    registry = resolve_service("task_registry")

    # Visibilidad SSOT = metadata.scope (sin listas hardcodeadas, SPEC-005 #7).
    # - Task inexistente → se deja que registry.execute emita el error estándar
    #   ('no registrada') en vez de confundirla con una interna.
    # - internal/system → bloqueadas desde entradas EXTERNAS (CLI/MCP: channel
    #   cli/mcp) ...
    # - ...pero el motor interno (trace_event_publisher, channel='system') sí
    #   las ejecuta. Sin esta puerta, los eventos CPG→trace de SPEC-002 G-W1
    #   se descartaban en silencio (regresión detectada comparando con
    #   capa_rout.md: el gate de visibilidad no debe romper adapters internos).
    info = registry.get_task_info(name)
    if info is not None:
        scope = info.get("scope", "public")
        if scope in ("internal", "system") and channel != "system":
            return {"ok": False, "blocked": True, "name": name}

    ctx = ExecutionContext.create(
        session_id=session_id, chain_dir=chain_dir, channel=channel
    )
    data = dict(task_data or {})
    data.setdefault("_project_id", project_id)
    payload = data.get("_payload", {})
    if isinstance(payload, dict):
        for k, v in payload.items():
            data.setdefault(k, v)

    result = registry.execute(name, ctx, data)
    result_data = result.data or {}
    if not result.ok:
        result_data["error"] = result.error or result_data.get(
            "error", "Task failed"
        )
        return {"ok": False, "error": result_data["error"], "data": result_data}
    return {"ok": True, "data": result_data}


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

    def _resolve_project_id(self) -> str:
        return ""

    def _ensure_chain_dir(self, project_id: str) -> str:
        return ""

    @safe
    def _exec() -> dict:
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

        # 3. Core compartido (mismo pipeline que handle_task, SPEC-005 #5)
        outcome = execute_public_task(
            name, project_id, chain_dir, body, session_id="mcp", channel="mcp"
        )
        if not outcome.get("ok"):
            if outcome.get("blocked"):
                return {
                    "type": "error",
                    "status_code": 400,
                    "message": (
                        f"Task '{name}' es INTERNA del workflow y no se invoca directamente."
                    ),
                }
            return {
                "type": "error",
                "status_code": 400,
                "message": outcome.get("error", f"Task '{name}' failed"),
                "data": {"result": outcome.get("data", {})},
            }
        return {
            "type": "task_result",
            "status_code": 200,
            "message": f"Task '{name}' executed successfully",
            "data": {"action": name, "ok": True, "result": outcome["data"]},
        }

    outcome = _exec()
    if is_successful(outcome):
        return outcome.value_or({})
    return {
        "type": "error",
        "status_code": 500,
        "message": f"Task '{name}' failed: {outcome.failure()}",
    }
