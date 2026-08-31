"""use_cases/approve_spec.py — ApproveSpecUseCase.

Aprobar una Spec con decisión arquitectónica (type=decision).
Aplica cuando TASK_REVIEW decide approval_type=decision.
Enriquece la Spec con decision, architecture, implementation y quality_gates.
"""
from __future__ import annotations

from shared.core.engine.domain.execution import ExecutionContext
from ._sprint_helpers import _spring_svc, _get_current_spec_id


class ApproveSpecUseCase:
    """Aprobar una Spec con decisión arquitectónica (type=decision)."""

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
            "status": "approved",
            "type": "decision",
            "decision": data.get("decision", {}),
            "architecture": data.get("architecture", {}),
            "implementation": data.get("implementation", {}),
            "quality_gates": data.get("quality_gates", []),
            "generated_from": data.get("generated_from", ""),
            "related_decisions": data.get("related_decisions", []),
        }

        updated = spring_svc.update_spec(spring_id, spec_id, updates)
        if not updated:
            return {"ok": False, "error": f"Spec {spec_id} not found"}

        return {
            "ok": True,
            "spec_id": spec_id,
            "status": "approved",
            "type": "decision",
            "message": f"Spec {spec_id} approved as decision.",
        }
