"""query task — thin adapter to QueryManager."""
from __future__ import annotations
import json
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from features.engine.context.query_manager import QueryManager


def query_task(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    raw = data.get("query", "")
    if not raw:
        return TaskResult.success(data={"message": "Task legacy. Usa 'walker <project>/kg_multi' con body JSON multi-capa",
            "example": 'walker <project>/kg_multi \'{"ckg":{"type":"decision"}}\'', "supported_layers": ["cpg", "ckg", "delta", "trace", "state"]})
    try:
        layers = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError) as e:
        return TaskResult.failure(f"Invalid JSON: {e}")
    if not isinstance(layers, dict):
        return TaskResult.failure("JSON must be an object with layer keys")
    qm = QueryManager(chain_dir, task_id=ctx.task_id or "", session_id=ctx.session_id or "")
    return TaskResult.success(data=qm.query(layers))


def register(registry: Any) -> None:
    registry.register("query", query_task,
                      metadata={"description": "Multi-layer query", "tags": ["query"]})
