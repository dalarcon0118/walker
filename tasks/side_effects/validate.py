"""Side-effect: validate_plan — valida todas las specs del plan.

Thin adapter: delega en ValidatePlanService (app/orchestration/).
"""

from __future__ import annotations
from typing import Any
from shared.common.shared_domain import ExecutionContext
from shared.core.engine.domain.side_effect_contract import as_side_effect, SCOPE_TRACKING
from returns.pipeline import is_successful
from returns.result import safe


@safe
def _validate_core(chain_dir: str) -> dict[str, Any]:
    """Valida el plan de forma pura. Puede lanzar excepciones."""
    from app.orchestration.validate_plan_service import ValidatePlanService

    return ValidatePlanService.validate_plan(chain_dir)


@as_side_effect(scope=SCOPE_TRACKING)
def validate_plan(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Thin adapter: delega en ValidatePlanService."""
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
        error = result.failure()
        return {
            "ok": False,
            "error": str(error),
            "summary": f"validate_plan: excepcion — {error}",
        }


def register(engine: Any) -> None:
    engine.register("validate_plan", validate_plan)
