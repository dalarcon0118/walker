"""use_cases/start_implementation.py — StartImplementationUseCase.

Iniciar implementación de una Spec.
Aplica cuando IMPLEMENT.USER_GATE autoriza el inicio.
Establece status=in_progress y agrega loop_verification, loop_acceptance.
"""
from __future__ import annotations

from shared.core.engine.domain.execution import ExecutionContext
from ._sprint_helpers import _spring_svc, _get_current_spec_id


class StartImplementationUseCase:
    """Iniciar implementación de una Spec."""

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        if not sprint_dir:
            raise ValueError("chain_dir not available")

        spring_svc = _spring_svc(sprint_dir)
        spring_id = spring_svc.get_spring_id(sprint_dir)
        spec_id = data.get("spec_id") or _get_current_spec_id(sprint_dir)
        if not spec_id:
            raise ValueError("spec_id not available")

        updates = {
            "status": "in_progress",
            "loop_verification": data.get("loop_verification", []),
            "loop_acceptance": data.get("loop_acceptance", "all"),
        }

        updated = spring_svc.update_spec(spring_id, spec_id, updates)
        if not updated:
            return {"ok": False, "error": f"Spec {spec_id} not found"}

        return {
            "ok": True,
            "spec_id": spec_id,
            "status": "in_progress",
            "message": f"Spec {spec_id} implementation started.",
        }
