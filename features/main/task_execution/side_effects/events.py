"""Event side-effects: emit_event."""
from __future__ import annotations
from typing import Any
from shared.common.shared_domain import ExecutionContext
from core.engine.domain.side_effect_contract import as_side_effect, ok, SCOPE_EVENT


@as_side_effect(scope=SCOPE_EVENT)
def emit_event(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Emite un evento de dominio (envelope estándar, scope=event).

    El campo `event` viaja en signals para que _dispatch_events_from_side_effects
    lo encuentre sin depender de la estructura interna del handler.
    """
    return ok(
        type="emit_event",
        scope=SCOPE_EVENT,
        summary=f"evento emitido: {params.get('event', 'unknown')}",
        signals={
            "event": params.get("event", "unknown"),
            "task_id": ctx.task_id,
            "timestamp": ctx.trace_id,
            "payload": dict(params.get("payload", {})),
        },
    )


def register(engine: Any) -> None:
    engine.register("emit_event", emit_event)
