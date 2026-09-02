"""handlers/trace.py — Event tracing tool handlers.

Handles: trace_push (push event to trace index).
"""
from __future__ import annotations

from typing import Any

from fast_mcp.features.event_tracing.application.services.trace_service import (
    TraceService,
)


def handle_trace_push(ctx: dict, **kwargs: Any) -> dict:
    """Push an event to the trace index."""
    trace_svc: TraceService | None = ctx.get("trace_service")
    if trace_svc is None:
        return {"error": "trace_service not wired"}

    chain_dir = kwargs.get("chain_dir", "")
    event_type = kwargs.get("event_type", "event")
    data = kwargs.get("data", {})
    task_id = kwargs.get("task_id", "")

    push_data = {
        "event_type": event_type,
        "source": kwargs.get("source", "walker_task"),
        "task_id": task_id,
        "sprint_id": kwargs.get("sprint_id", ""),
        "node": kwargs.get("node", ""),
        "tool": kwargs.get("tool", ""),
        "input": kwargs.get("input", {}),
        "output": kwargs.get("output", {}),
        "files": kwargs.get("files", []),
        "data": data,
    }

    result = trace_svc.push_event(chain_dir, push_data)
    return result
