"""Event side-effects: emit_event."""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext


def emit_event(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "event": params.get("event", "unknown"), "task_id": ctx.task_id, "timestamp": ctx.trace_id}


def register(engine: Any) -> None:
    engine.register("emit_event", emit_event)
