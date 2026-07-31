"""Side-effect: validate_plan — valida todas las specs del plan.
    
Se invoca desde side_effects de nodos como TASK_SPEC al completar.
"""
from __future__ import annotations
from typing import Any
from features.engine.domain.models import ExecutionContext
from features.spring.application.use_cases import ValidatePlanUseCase


def validate_plan(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    try:
        result = ValidatePlanUseCase.execute(ctx, params)
        return {"ok": result.get("valid", False), "valid": result.get("valid", False),
                "errors": result.get("errors", []), "spec_count": result.get("spec_count", 0),
                "specified_count": result.get("specified_count", 0)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def register(engine: Any) -> None:
    engine.register("validate_plan", validate_plan)
