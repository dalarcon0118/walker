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

from shared.core.engine.domain.guards import ScopeGuard


def execute_public_task(
    name: str,
    project_id: str,
    chain_dir: str,
    task_data: dict,
    session_id: str = "cli",
    channel: str = "cli",
    registry: Any = None,
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
    from shared.common.shared_domain import ExecutionContext

    if registry is None:
        raise ValueError("execute_public_task: registry no inyectado (composition root required)")

    # Visibilidad SSOT = ScopeGuard (SPEC-005 #7, ADR-WALKER-060).
    # - Task inexistente → se deja que registry.execute emita el error estándar.
    # - internal/system → bloqueadas desde entradas EXTERNAS (CLI/MCP: channel
    #   cli/mcp). El motor interno (trace_event_publisher, channel='system') sí
    #   las ejecuta. Sin esta puerta, los eventos CPG→trace de SPEC-002 G-W1
    #   se descartaban en silencio.
    if ScopeGuard.is_blocked(name, channel, registry):
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



