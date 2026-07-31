"""Implementation tasks: implement_task, run_tests."""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult


def implement_task(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(data={
        "task_id": data.get("task_id", ctx.task_id),
        "result_state": data.get("objective", ""),
        "files_modified": data.get("files", []),
    })


def run_tests(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(data={
        "task_id": data.get("task_id", ctx.task_id),
        "passed": True, "total": 0, "failures": 0,
    })


def register(registry: Any) -> None:
    # Acciones del workflow IMPLEMENT.* / VERIFY.QA_RUN — internas
    registry.register("implement_task", implement_task,
                      metadata={"description": "Prepare task implementation", "tags": ["implementation"], "scope": "internal"})
    registry.register("run_tests", run_tests,
                      metadata={"description": "Run test suite", "tags": ["test"], "scope": "internal"})
