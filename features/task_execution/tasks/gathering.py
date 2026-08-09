"""Context gathering tasks — internal workflow steps via ICpgAnalysisPort + CKG."""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from libs.result_types import is_successful
from features.cognitive_knowledge.application.use_cases import GatherCkgContextUseCase


def _ckg_context(chain_dir: str, task_id: str = "") -> list:
    result = GatherCkgContextUseCase.execute(chain_dir, task_id=task_id)
    if not is_successful(result):
        return []
    return result.value_or([])


def _cpg_port(chain_dir: str) -> Any:
    from features.task_execution.application.cpg_port_resolver import resolve_cpg_port
    return resolve_cpg_port(chain_dir)


def gather_context(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    files = data.get("files", [])
    port = _cpg_port(chain_dir)
    cpg_result = port.gather_context(files) if port else {}
    context = {
        "cpg": cpg_result,
        "ckg": _ckg_context(chain_dir),
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
    port = _cpg_port(chain_dir)
    cpg_result = port.gather_context(files) if (files and port) else {}
    context = {
        "cpg": cpg_result,
        "ckg": _ckg_context(chain_dir, task_id),
        "task_id": task_id, "input": data.get("context", {}),
    }
    return TaskResult.success(data={"task_id": task_id, "context": context,
                                     "files_analyzed": len(files)})


def register(registry: Any) -> None:
    registry.register("gather_context", gather_context,
                      metadata={"description": "Gather context from CPG + CKG", "tags": ["gathering"], "scope": "internal"})
    registry.register("gather_task_context", gather_task_context,
                      metadata={"description": "Gather task-specific context", "tags": ["gathering"], "scope": "internal"})
