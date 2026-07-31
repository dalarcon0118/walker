"""cpg_unused_functions — thin adapter to code_analysis use case."""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from features.code_analysis.application.use_cases import DetectUnusedFunctionsUseCase


def cpg_unused_functions(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    result = DetectUnusedFunctionsUseCase.execute(chain_dir, data.get("file", ""))
    return TaskResult.success(data=result)


def register(registry: Any) -> None:
    registry.register("cpg_unused_functions", cpg_unused_functions,
                      metadata={"description": "Detect unused functions via CPG", "tags": ["cpg"]})
