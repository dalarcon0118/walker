"""handlers/trace.py — Event tracing tool handlers.

Handles: trace_push (push event to trace index),
trace_message (record user/agent messages — T-14).
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
        "sub_state": kwargs.get("sub_state", ""),
        "tool": kwargs.get("tool", ""),
        "input": kwargs.get("input", {}),
        "output": kwargs.get("output", {}),
        "files": kwargs.get("files", []),
        "data": data,
        # Sin esto, los eventos del plugin (que manda session_id/agent_id)
        # llegaban sin forma de correlacionarse con la sesión.
        "session_id": kwargs.get("session_id", ""),
        "agent_id": kwargs.get("agent_id", ""),
        "metadata": kwargs.get("metadata", {}) or {},
    }

    result = trace_svc.push_event(chain_dir, push_data)
    return result


def handle_trace_message(ctx: dict, **kwargs: Any) -> dict:
    """Record a user/agent message in the trace index (T-14).

    Cierra la brecha del propósito declarado (project.md, taxonomía de
    memorias): TraceGraph debe permitir entender los mensajes enviados por
    el usuario y el agente. Persiste event_type 'user_message'/'agent_message'
    con el texto en data (campos: role, text, session_id).

    Fail-fast: role inválido o text vacío → error estructurado, sin escribir.
    """
    trace_svc: TraceService | None = ctx.get("trace_service")
    if trace_svc is None:
        return {"error": "trace_service not wired"}

    role = kwargs.get("role", "")
    if role not in ("user", "agent"):
        return {"error": "trace_message: role must be 'user' or 'agent'"}
    text = str(kwargs.get("text", "") or "").strip()
    if not text:
        return {"error": "trace_message: text is required"}

    push_data = {
        "event_type": f"{role}_message",
        "source": kwargs.get("source", "walker"),
        "task_id": kwargs.get("task_id", ""),
        "sprint_id": kwargs.get("sprint_id", ""),
        "data": {
            "role": role,
            "text": text,
            "session_id": kwargs.get("session_id", ""),
        },
    }
    return trace_svc.push_event(kwargs.get("chain_dir", ""), push_data)
