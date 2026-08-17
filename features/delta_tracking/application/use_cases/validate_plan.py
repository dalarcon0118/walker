"""use_cases/validate_plan.py — ValidatePlanUseCase.

Valida todas las specs del delta (fuente unica).
"""
from __future__ import annotations

from shared.common.shared_domain import ExecutionContext
from ...domain.services.spec_service import SpecValidator
from ._sprint_helpers import _spring_svc


class ValidatePlanUseCase:
    """Validates all specifications from the delta (fuente unica)."""

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        try:
            spring_svc = _spring_svc(sprint_dir)
            spring_id = spring_svc.get_spring_id(sprint_dir)
            spring_svc.ensure_spring_imported(sprint_dir, spring_id)
            specs = spring_svc.get_specs_legacy(spring_id)
        except ValueError:
            return {
                "valid": False,
                "errors": ["spring not found in delta"],
                "spec_count": 0,
            }
        if not specs:
            return {
                "valid": False,
                "errors": ["No specifications in spring"],
                "spec_count": 0,
            }
        all_errors: list[str] = []
        for spec in specs:
            all_errors.extend(SpecValidator.validate_spec(spec))
        return {
            "valid": len(all_errors) == 0,
            "errors": all_errors,
            "spec_count": len(specs),
            "specified_count": sum(1 for s in specs if s.get("status") == "specified"),
        }
