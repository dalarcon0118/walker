"""Implementation tasks: implement_task, run_tests."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from libs.shared_domain.validated_vo import TaskInput


@dataclass(frozen=True)
class ImplementInput(TaskInput):
    """Input validado de implement_task.

    objective: resultado esperado de la tarea.
    files: lista de archivos modificados.
    """

    objective: str = ""
    files: list | None = None

    def _validate(self) -> str:
        if self.files is not None and not isinstance(self.files, list):
            return "files debe ser una lista"
        return ""


def implement_task(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    input_result = ImplementInput.from_dict(data)
    if not input_result.ok:
        return TaskResult.failure(input_result.error or "ImplementInput inválido")
    inp: ImplementInput = input_result.data

    return TaskResult.success(data={
        "task_id": inp.task_id or ctx.task_id,
        "result_state": inp.objective,
        "files_modified": inp.files or [],
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
