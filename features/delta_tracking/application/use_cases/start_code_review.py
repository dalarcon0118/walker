"""use_cases/start_code_review.py — StartCodeReviewUseCase.

Iniciar code review de una Spec.
Aplica cuando IMPLEMENT.CODE_REVIEW_SCAN ejecuta el análisis.
Establece status=in_review y agrega review_metrics.
"""
from __future__ import annotations

from shared.common.shared_domain import ExecutionContext
from ._sprint_helpers import _spring_svc, _get_current_spec_id


class StartCodeReviewUseCase:
    """Iniciar code review de una Spec."""

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
            "status": "in_review",
            "review_metrics": data.get("review_metrics", {}),
        }

        updated = spring_svc.update_spec(spring_id, spec_id, updates)
        if not updated:
            return {"ok": False, "error": f"Spec {spec_id} not found"}

        return {
            "ok": True,
            "spec_id": spec_id,
            "status": "in_review",
            "message": f"Spec {spec_id} code review started.",
        }
