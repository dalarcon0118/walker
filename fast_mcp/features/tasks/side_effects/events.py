"""side_effects/events.py — efecto `emit_event`.

El grafo emite eventos de dominio en ANALYZE/REFINE/SPRINT/VERIFY, pero el
efecto no existía: el motor devolvía "unknown side effect" y el TraceGraph
perdía los hitos (`sprint_planned`, `task_completed`, `sprint_closed`...).

Escribe en el TraceGraph del proyecto. Fail-open: trazar nunca debe frenar el
workflow, pero el resultado dice explícitamente si se persistió.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def emit_event(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    from ._context import session_id_of, state_context, task_id_of

    params = params or {}
    event = str(params.get("event") or "event")
    task_id = task_id_of(params, kwargs)
    session_id = session_id_of(params, kwargs)
    ctx_data = state_context(kwargs)

    data = {
        "event": event,
        "task_id": task_id,
        "session_id": session_id,
        "payload": dict(params.get("payload") or {}),
    }
    try:
        from fast_mcp.composition import trace_tool_runner

        result = trace_tool_runner("trace_push")(
            chain_dir=chain_dir,
            event_type=event,
            source="workflow",
            task_id=task_id,
            sprint_id=str(ctx_data.get("spring_id") or params.get("spring_id") or ""),
            session_id=session_id,
            data=data,
        )
    except Exception as exc:  # noqa: BLE001 — trazar es best-effort
        logger.warning("emit_event(%s) falló: %s: %s", event, type(exc).__name__, exc)
        return {
            "ok": True,
            "skipped": True,
            "persisted": False,
            "event": event,
            "reason": f"trace no disponible: {type(exc).__name__}: {exc}",
            "error": str(exc),
        }

    if isinstance(result, dict) and result.get("error"):
        return {
            "ok": True,
            "skipped": True,
            "persisted": False,
            "event": event,
            "reason": f"trace rechazó el evento: {result['error']}",
            "error": str(result["error"]),
        }
    return {"ok": True, "event": event, "emitted": True, "result": result}


def register(registry) -> None:
    registry.register("emit_event", emit_event)
