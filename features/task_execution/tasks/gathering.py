"""Context gathering tasks — thin adapters to code_analysis/ + cognitive_knowledge/ use cases."""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from features.code_analysis.application.use_cases import GatherCpgContextUseCase
from features.cognitive_knowledge.application.use_cases import GatherCkgContextUseCase


def gather_context(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    files = data.get("files", [])
    context = {
        "cpg": GatherCpgContextUseCase.execute(chain_dir, files),
        "ckg": GatherCkgContextUseCase.execute(chain_dir),
        "input": data.get("context", {}),
    }
    return TaskResult.success(data={"task_id": data.get("task_id", ctx.task_id),
                                     "context": context, "files_analyzed": len(files)})


def gather_task_context(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    files = data.get("files", [])
    task_id = data.get("task_id", ctx.task_id)
    context = {
        "cpg": GatherCpgContextUseCase.execute(chain_dir, files) if files else {},
        "ckg": GatherCkgContextUseCase.execute(chain_dir, task_id=task_id),
        "task_id": task_id, "input": data.get("context", {}),
    }
    return TaskResult.success(data={"task_id": task_id, "context": context,
                                     "files_analyzed": len(files)})


def register(registry: Any) -> None:
    # Acciones del workflow (DESIGN_GATHER, etc.) — internas: solo el motor las ejecuta
    registry.register("gather_context", gather_context,
                      metadata={"description": "Gather context from CPG + CKG", "tags": ["gathering"], "scope": "internal"})
    registry.register("gather_task_context", gather_task_context,
                      metadata={"description": "Gather task-specific context", "tags": ["gathering"], "scope": "internal"})
