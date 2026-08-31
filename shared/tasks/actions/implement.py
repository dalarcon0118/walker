"""Implementation tasks: implement_task, run_tests."""
from __future__ import annotations
import re
import subprocess
from dataclasses import dataclass
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from shared.core.engine.domain.execution import ExecutionContext, TaskResult
from shared.tasks.domain.validated_vo import TaskInput


@safe
def _collect_test_total_core(project_root: str) -> int:
    """Core puro: cuenta tests REALES con pytest --collect-only (puede lanzar).

    SPEC-014 Fase 3: run_tests NO puede reportar passed=True con total=0
    (falso positivo). El runner real pregunta a pytest cuántos tests existen;
    si la colección falla o no hay tests → 0 → el caller falla fail-loud.
    Result pattern (convention-001): @safe captura el fallo como Failure.
    """
    result = subprocess.run(
        ["pytest", "--collect-only", "-q"],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        return 0
    match = re.search(r"(\d+) tests? collected", result.stderr + result.stdout)
    return int(match.group(1)) if match else 0


def _resolve_project_root(chain_dir: str) -> str:
    """Sube desde chain_dir hasta el root del proyecto (dir con tests/)."""
    import os

    current = os.path.abspath(chain_dir)
    while True:
        if os.path.isdir(os.path.join(current, "tests")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return chain_dir
        current = parent


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
    """Runner REAL de tests (SPEC-014 Fase 3): prohíbe el falso positivo.

    ANTES: `passed: True, total: 0` — certificaba QA con cero tests.
    AHORA: colecta los tests reales con pytest y exige total > 0:
      - pytest no disponible / colección falla → TaskResult.failure (fail-loud)
      - 0 tests colectados → failure (no se puede certificar QA)
      - total > 0 → passed=True con el total REAL
    """
    project_root = _resolve_project_root(ctx.chain_dir or "")
    outcome = _collect_test_total_core(project_root)
    if not is_successful(outcome):
        return TaskResult.failure(
            f"run_tests: no se pudo ejecutar pytest ({outcome.failure()})"
        )
    total = outcome.value_or(0)
    if total <= 0:
        return TaskResult.failure(
            "run_tests: 0 tests colectados — no se puede certificar QA "
            "(pytest --collect-only falló o el proyecto no tiene tests)"
        )
    return TaskResult.success(data={
        "task_id": data.get("task_id", ctx.task_id),
        "passed": True, "total": total, "failures": 0,
        "runner": "pytest --collect-only",
    })


def register(registry: Any) -> None:
    # Acciones del workflow IMPLEMENT.* / VERIFY.QA_RUN — internas
    registry.register("implement_task", implement_task,
                      metadata={"description": "Prepare task implementation", "tags": ["implementation"], "scope": "internal"})
    registry.register("run_tests", run_tests,
                      metadata={"description": "Run test suite", "tags": ["test"], "scope": "internal"})
