"""Code review task — thin adapter to code_analysis use case.

Recibe un input TIPADO y VALIDADO (ReviewInput) en lugar de un dict crudo:
la lógica de negocio solo ve entidades limpias y verificadas.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from libs.shared_domain.validated_vo import TaskInput
from features.code_analysis.application.use_cases import ReviewCodeUseCase


@dataclass(frozen=True)
class ReviewInput(TaskInput):
    """Input validado de review_code.

    files: lista de paths a revisar (None → todos los del proyecto).
    thresholds: umbrales de calidad (opcional, dict).
    """

    files: list | None = None
    thresholds: dict | None = None

    def _validate(self) -> str:
        if self.files is not None and not isinstance(self.files, list):
            return "files debe ser una lista de paths"
        if self.thresholds is not None and not isinstance(self.thresholds, dict):
            return "thresholds debe ser un dict"
        return ""


def review_code(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")

    # Input validado: la lógica de negocio recibe una entidad limpia
    input_result = ReviewInput.from_dict(data)
    if not input_result.ok:
        return TaskResult.failure(input_result.error or "ReviewInput inválido")
    inp: ReviewInput = input_result.data

    try:
        result = ReviewCodeUseCase.execute(
            chain_dir, inp.files, inp.thresholds
        )
        result["task_id"] = inp.task_id or ctx.task_id
        return TaskResult.success(data=result)
    except Exception as e:
        return TaskResult.failure(f"Review error: {e}")


def register(registry: Any) -> None:
    # Accion del workflow IMPLEMENT.CODE_REVIEW — interna
    registry.register("review_code", review_code,
                      metadata={"description": "Code review via CPG", "tags": ["review", "cpg"], "scope": "internal"})
