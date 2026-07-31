"""Task: trace_push — registra eventos en el TraceGraph."""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from features.project import ProjectService


def trace_push(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")

    from libs.infrastructure import CognitiveGraphSearcher

    paths = ProjectService.resolve_paths(chain_dir)
    cg = CognitiveGraphSearcher(paths.project_root)
    ts = datetime.now(timezone.utc).isoformat()

    event_id = f"ev-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    event_type = data.get("event_type", "event")
    source = data.get("source", "walker_task")

    raw_input = data.get("input", "{}")
    raw_output = data.get("output", "{}")
    try:
        input_data = json.loads(raw_input) if isinstance(raw_input, str) else raw_input
    except Exception:
        input_data = {"raw": raw_input}
    try:
        output_data = json.loads(raw_output) if isinstance(raw_output, str) else raw_output
    except Exception:
        output_data = {"raw": raw_output}

    files_raw = data.get("files", [])
    if isinstance(files_raw, str):
        try:
            files_raw = json.loads(files_raw)
        except Exception:
            files_raw = [files_raw]

    event = {
        "event_type": event_type, "source": source,
        "agent_id": os.environ.get("WALKER_AGENT_ID", ""),
        "session_id": os.environ.get("WALKER_SESSION_ID", ctx.session_id),
        "sprint_id": data.get("sprint_id", ""),
        "task_id": data.get("task_id", ctx.task_id or ""),
        "node": data.get("node", ""), "sub_state": data.get("sub_state", ""),
        "input": input_data, "output": output_data,
        "files": files_raw if isinstance(files_raw, list) else [],
        "started_at": data.get("started_at", ts), "finished_at": ts,
        "duration_ms": data.get("duration_ms", 0),
        "metadata": data.get("metadata", {}),
    }

    cg.store("trace", event_id, event)
    return TaskResult.success(data={"ok": True, "event_id": event_id, "event_type": event_type})


def register(registry: Any) -> None:
    registry.register("trace_push", trace_push,
                      metadata={"description": "Push events to TraceGraph", "tags": ["trace", "events"]})
