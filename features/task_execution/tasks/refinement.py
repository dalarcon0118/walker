"""Refinement tasks: refine_diagnose, route_to_target, escalate_refine."""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult


def refine_diagnose(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(data={"task_id": data.get("task_id", ctx.task_id), "target": data.get("target", "analyze"), "reason": data.get("reason", ""), "knowledge_items": data.get("knowledge_items", [])})


def route_to_target(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(data={"task_id": data.get("task_id", ctx.task_id), "target": data.get("target", "analyze"), "routed": True})


def escalate_refine(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(data={"task_id": data.get("task_id", ctx.task_id), "ok": True, "previous_attempts": data.get("attempt", 3), "message": f"Refine limit reached after {data.get('attempt', 3)} attempts"})


def register(registry: Any) -> None:
    # Acciones del workflow REFINE.* — internas: solo el motor las ejecuta
    registry.register("refine_diagnose", refine_diagnose, metadata={"description": "Diagnose root cause for refinement", "tags": ["refine"], "scope": "internal"})
    registry.register("route_to_target", route_to_target, metadata={"description": "Route to refinement node", "tags": ["refine"], "scope": "internal"})
    registry.register("escalate_refine", escalate_refine, metadata={"description": "Escalate refinement when limit reached", "tags": ["refine"], "scope": "internal"})
