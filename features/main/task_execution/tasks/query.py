"""query task — thin adapter to QueryManager."""
from __future__ import annotations
import json
from typing import Any
from returns.pipeline import is_successful
from returns.result import safe
from shared.common.shared_domain import ExecutionContext, TaskResult
from core.engine.context.query_manager import QueryManager


@safe
def _parse_layers(raw: str | dict) -> dict:
    """Core puro: parsea el body JSON multi-capa (puede lanzar JSONDecodeError)."""
    return json.loads(raw) if isinstance(raw, str) else raw


def query_task(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    raw = data.get("query", "")
    if not raw:
        return TaskResult.success(data={"message": "Task legacy. Usa 'walker <project>/kg_multi' con body JSON multi-capa",
            "example": 'walker <project>/kg_multi \'{"ckg":{"type":"decision"}}\'', "supported_layers": ["cpg", "ckg", "delta", "trace", "state"]})

    # Result pattern (convention-001): el core @safe nunca lanza; ante JSON
    # inválido devuelve Failure y el wrapper decide con is_successful.
    parsed = _parse_layers(raw)
    if not is_successful(parsed):
        return TaskResult.failure(f"Invalid JSON: {parsed.failure()}")
    layers = parsed.value_or({})
    if not isinstance(layers, dict):
        return TaskResult.failure("JSON must be an object with layer keys")
    qm = QueryManager(chain_dir, task_id=ctx.task_id or "", session_id=ctx.session_id or "")
    return TaskResult.success(data=qm.query(layers))


def register(registry: Any) -> None:
    registry.register("query", query_task,
                      metadata={"description": "Multi-layer query", "tags": ["query"],
                                "input_schema": {
                                    "type": "object",
                                    "properties": {
                                        "query": {"type": "string", "description": "JSON multi-capa en string (capas: cpg, ckg, delta, trace, state). Legacy; si vacío sugiere kg_multi", "example": "{\"ckg\":{\"type\":\"decision\"}}"},
                                    },
                                    "required": [],
                                }})
