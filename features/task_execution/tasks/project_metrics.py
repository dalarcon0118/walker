"""Project metrics task — thin adapter to code_analysis use case."""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from features.code_analysis.application.use_cases import ProjectMetricsUseCase


def project_metrics(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    try:
        result = ProjectMetricsUseCase.execute(chain_dir, data.get("file", ""))
        return TaskResult.success(data=result)
    except Exception as e:
        return TaskResult.failure(f"Metrics error: {e}")


def register(registry: Any) -> None:
    registry.register("project_metrics", project_metrics,
                      metadata={"description": "Compute project metrics from CPG", "tags": ["cpg", "metrics"]})
