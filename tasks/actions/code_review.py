"""Code review task — internal workflow step via ICpgAnalysisPort.

Recibe un input TIPADO y VALIDADO (ReviewInput) en lugar de un dict crudo:
la lógica de negocio solo ve entidades limpias y verificadas.

Result pattern (dry-python/returns): los cores con @safe nunca lanzan; los
wrappers deciden con is_successful y hacen fallback explícito con log
(convention-001: nunca except silencioso).
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Any
from returns.pipeline import is_successful
from returns.result import safe
from shared.common.shared_domain import ExecutionContext, TaskResult
from shared.common.shared_domain.validated_vo import TaskInput

logger = logging.getLogger(__name__)


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


@safe
def _resolve_default_port(chain_dir: str) -> Any:
    """Core puro: resuelve el port de análisis CPG (puede lanzar)."""
    from tasks.application.cpg_port_resolver import resolve_cpg_port
    return resolve_cpg_port(chain_dir)


@safe
def _review_core(port: Any, files: list | None, thresholds: dict | None, task_id: str) -> dict:
    """Core puro: ejecuta la revisión vía port (puede lanzar)."""
    result = port.review_code(files, thresholds)
    result["task_id"] = task_id
    return result


def review_code(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")

    input_result = ReviewInput.from_dict(data)
    if not input_result.ok:
        return TaskResult.failure(input_result.error or "ReviewInput inválido")
    inp: ReviewInput = input_result.data

    resolved = _resolve_default_port(chain_dir)
    if not is_successful(resolved):
        error = resolved.failure()
        logger.warning(
            "review_code: no se pudo resolver el port CPG (%s: %s)",
            type(error).__name__, error,
        )
        return TaskResult.failure("CpgAnalysisPort not available")
    port = resolved.value_or(None)
    if port is None:
        return TaskResult.failure("CpgAnalysisPort not available")

    reviewed = _review_core(port, inp.files, inp.thresholds, inp.task_id or ctx.task_id)
    if not is_successful(reviewed):
        error = reviewed.failure()
        logger.warning(
            "review_code fallo (%s: %s)", type(error).__name__, error,
        )
        return TaskResult.failure(f"Review error: {error}")
    return TaskResult.success(data=reviewed.value_or({}))


def register(registry: Any) -> None:
    registry.register("review_code", review_code,
                      metadata={"description": "Code review via CPG", "tags": ["review", "cpg"], "scope": "internal"})
