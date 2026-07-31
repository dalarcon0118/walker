"""Code review task — thin adapter to code_analysis use case."""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from features.code_analysis.application.use_cases import ReviewCodeUseCase


def review_code(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    try:
        result = ReviewCodeUseCase.execute(chain_dir, data.get("files"), data.get("thresholds"))
        result["task_id"] = data.get("task_id", ctx.task_id)
        return TaskResult.success(data=result)
    except Exception as e:
        return TaskResult.failure(f"Review error: {e}")


def register(registry: Any) -> None:
    # Accion del workflow IMPLEMENT.CODE_REVIEW — interna
    registry.register("review_code", review_code,
                      metadata={"description": "Code review via CPG", "tags": ["review", "cpg"], "scope": "internal"})
