"""Verification tasks: verify_predicates.

Wrappers de workflow sobre el motor real (features/task_execution/
infrastructure/verify_engine.py). El motor verifica predicates contra
código real (ast-grep / grep / shell / pytest) con cache de sesión.

Recibe un input TIPADO y VALIDADO (VerifyInput) en lugar de un dict crudo.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from libs.shared_domain.validated_vo import TaskInput

from features.task_execution.infrastructure.verify_engine import (
    invalidate_verify_cache,
    verify_predicates_batch,
)


@dataclass(frozen=True)
class VerifyInput(TaskInput):
    """Input validado de verify_predicates.

    predicates: lista de strings o dicts tipados (o data["predicate"] single).
    project_root: ruta del proyecto (override del chain_dir).
    fresh: True → limpiar cache de verificación.
    """

    predicates: list | None = None
    predicate: Any = None
    project_root: str = ""
    fresh: bool = False

    def _validate(self) -> str:
        if self.predicates is None and self.predicate is None:
            return "se requiere 'predicates' o 'predicate'"
        if self.predicates is not None and not isinstance(self.predicates, list):
            return "predicates debe ser una lista"
        return ""

    def normalized_predicates(self) -> list:
        """Aplana a la forma que espera el motor.

        Acepta:
          - predicates: lista de strings o dicts tipados
          - predicate: single string/dict
          - items loop_verification: {"predicate": ..., "type": ...}
            → extrae el campo "predicate"
        """
        raw = self.predicates
        if raw is None:
            raw = [self.predicate] if self.predicate is not None else []

        predicates: list = []
        for item in raw:
            if isinstance(item, dict) and "predicate" in item:
                predicates.append(item["predicate"])
            else:
                predicates.append(item)
        return predicates


def _normalize_predicates(data: dict[str, Any]) -> list:
    """Aplana la entrada de predicates a la forma que espera el motor.

    Acepta:
      - data["predicates"]: lista de strings o dicts tipados
      - data["predicate"]: single string/dict
      - items con formato loop_verification: {"predicate": ..., "type": ...}
        → extrae el campo "predicate" (el type se interpreta en el motor
          a través del formato dict tipado, no del loop_item)
    """
    raw = data.get("predicates")
    if raw is None:
        single = data.get("predicate")
        raw = [single] if single is not None else []

    predicates: list = []
    for item in raw:
        if isinstance(item, dict) and "predicate" in item:
            # loop_verification item → predicate plano para el motor
            predicates.append(item["predicate"])
        else:
            predicates.append(item)
    return predicates


def verify_predicates(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """VERIFY.PREDICATE_CHECK — Verifica DoD predicates contra código real.

    Args:
        ctx: ExecutionContext (usa ctx.chain_dir como raíz si no viene
            project_root en data).
        data: {
            "predicates": [...] | "predicate": "...",
            "project_root": ruta (opcional, override),
            "task_id": str (opcional),
            "_fresh": bool (opcional, true → limpiar cache)
        }

    Returns:
        TaskResult.success con:
            task_id, all_passed, results (detalle por predicate),
            failed_count, passed_count, total
    """
    # Input validado: la lógica de negocio recibe una entidad limpia
    if data.get("_fresh"):
        invalidate_verify_cache()

    input_result = VerifyInput.from_dict(data)
    if not input_result.ok:
        # Si no hay predicates, no es error: es un no-op válido
        if data.get("predicates") is None and data.get("predicate") is None:
            return TaskResult.success(data={
                "task_id": data.get("task_id", ctx.task_id),
                "all_passed": True,
                "results": [],
                "failed_count": 0,
                "passed_count": 0,
                "total": 0,
            })
        return TaskResult.failure(input_result.error or "VerifyInput inválido")
    inp: VerifyInput = input_result.data

    if inp.fresh:
        invalidate_verify_cache()

    predicates = inp.normalized_predicates()
    project_root = inp.project_root or ctx.chain_dir or ""
    if not project_root:
        return TaskResult.failure("project_root/chain_dir no disponible")

    if not predicates:
        return TaskResult.success(data={
            "task_id": inp.task_id or ctx.task_id,
            "all_passed": True,
            "results": [],
            "failed_count": 0,
            "passed_count": 0,
            "total": 0,
        })

    results = verify_predicates_batch(predicates, project_root)

    detailed = []
    failed = 0
    for pred, (passed, info) in zip(predicates, results):
        if not passed:
            failed += 1
        detailed.append({
            "predicate": info.get("label") or (pred if isinstance(pred, str) else str(pred)),
            "method": info.get("method", "unknown"),
            "passed": passed,
            "detail": info,
        })

    return TaskResult.success(data={
        "task_id": inp.task_id or ctx.task_id,
        "all_passed": failed == 0,
        "results": detailed,
        "failed_count": failed,
        "passed_count": len(detailed) - failed,
        "total": len(detailed),
        "project_root": project_root,
    })


def register(registry: Any) -> None:
    # Accion del workflow VERIFY.PREDICATE_CHECK — interna
    registry.register("verify_predicates", verify_predicates,
                      metadata={"description": "Verify DoD predicates against code", "tags": ["verification"], "scope": "internal"})
