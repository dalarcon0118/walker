"""Side-effect: validate_plan — valida todas las specs del plan.

Se invoca desde side_effects de nodos como TASK_SPEC al completar.
Si la validacion falla, el feedback incluye los errores especificos
para que el agente sepa exactamente que esta mal y como corregirlo.

Refactorizado para usar Result pattern (dry-python/returns).
"""

from __future__ import annotations
from typing import Any

from returns.result import safe
from returns.pipeline import is_successful

from core.engine.domain.models import ExecutionContext
from core.engine.domain.side_effect_contract import as_side_effect, SCOPE_TRACKING
from features.delta_tracking.application.use_cases_sprint import ValidatePlanUseCase


@safe
def _validate_core(chain_dir: str) -> dict[str, Any]:
    """Valida el plan de forma pura. Puede lanzar excepciones.

    Returns:
        dict con valid, errors, spec_count, specified_count

    Raises:
        FileNotFoundError: si el spring no existe en el DeltaGraph
        Exception: otros errores de validacion
    """
    ctx = ExecutionContext.create(session_id="", chain_dir=chain_dir)
    result = ValidatePlanUseCase.execute(ctx, {})
    valid = result.get("valid", False)
    errors = result.get("errors", [])
    spec_count = result.get("spec_count", 0)
    specified_count = result.get("specified_count", 0)
    return {
        "ok": valid,
        "valid": valid,
        "errors": errors,
        "spec_count": spec_count,
        "specified_count": specified_count,
    }


@as_side_effect(scope=SCOPE_TRACKING)
def validate_plan(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Wrapper que usa Result pattern para manejo explícito de errores."""
    # Ejecutar validación con @safe (convierte excepciones en Failure)
    result = _validate_core(ctx.chain_dir or "")

    if is_successful(result):
        data = result.value_or({})
        valid = data.get("valid", False)
        errors = data.get("errors", [])
        spec_count = data.get("spec_count", 0)
        specified_count = data.get("specified_count", 0)

        ret: dict[str, Any] = {
            "ok": valid,
            "valid": valid,
            "errors": errors,
            "spec_count": spec_count,
            "specified_count": specified_count,
        }
        if not valid and errors:
            ret["summary"] = (
                f"validate_plan: {len(errors)} error(es) en {specified_count}/{spec_count} specs — "
                + "; ".join(errors[:5])
            )
        return ret
    else:
        # Failure: excepción capturada por @safe
        error = result.failure()
        return {
            "ok": False,
            "error": str(error),
            "summary": f"validate_plan: excepcion — {error}",
        }


def register(engine: Any) -> None:
    engine.register("validate_plan", validate_plan)
