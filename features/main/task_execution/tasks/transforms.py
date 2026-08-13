"""Edge transform tasks: data pipelines between nodes."""
from __future__ import annotations
from typing import Any
from shared.common.shared_domain import ExecutionContext, TaskResult


def prepare_analysis_context(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(data={"input": data.get("input", {}), "output": data.get("output", {}), "transformed": True})


def build_task_briefing(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(data={"input": data.get("input", {}), "output": data.get("output", {}), "transformed": True})


def build_verification_context(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(data={"input": data.get("input", {}), "output": data.get("output", {}), "transformed": True})


def register(registry: Any) -> None:
    # Transformaciones de contexto entre fases del workflow — internas
    registry.register("prepare_analysis_context", prepare_analysis_context, metadata={"description": "Transform context from GATHER to ANALYSIS", "tags": ["transform"], "scope": "internal"})
    registry.register("build_task_briefing", build_task_briefing, metadata={"description": "Build task briefing from analysis", "tags": ["transform"], "scope": "internal"})
    registry.register("build_verification_context", build_verification_context, metadata={"description": "Prepare verification context", "tags": ["transform"], "scope": "internal"})
