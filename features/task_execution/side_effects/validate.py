"""Side-effect: validate_plan — valida todas las specs del plan.

Se invoca desde side_effects de nodos como TASK_SPEC al completar.
Si la validacion falla, el feedback incluye los errores especificos
para que el agente sepa exactamente que esta mal y como corregirlo.
"""
from __future__ import annotations
from typing import Any
from features.engine.domain.models import ExecutionContext
from features.engine.domain.side_effect_contract import as_side_effect, SCOPE_TRACKING
from features.spring.application.use_cases import ValidatePlanUseCase


@as_side_effect(scope=SCOPE_TRACKING)
def validate_plan(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    try:
        result = ValidatePlanUseCase.execute(ctx, params)
        valid = result.get("valid", False)
        errors = result.get("errors", [])
        spec_count = result.get("spec_count", 0)
        specified_count = result.get("specified_count", 0)
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
    except Exception as e:
        return {"ok": False, "error": str(e), "summary": f"validate_plan: excepcion — {e}"}


def register(engine: Any) -> None:
    engine.register("validate_plan", validate_plan)
