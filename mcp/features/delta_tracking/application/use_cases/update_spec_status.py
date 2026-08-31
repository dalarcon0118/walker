"""use_cases/update_spec_status.py — UpdateSpecStatusUseCase.

Actualiza el status de una spec: draft → specified → in_progress → completed.
"""
from __future__ import annotations

from shared.core.engine.domain.execution import ExecutionContext
from ._sprint_helpers import _spring_svc


class UpdateSpecStatusUseCase:
    """Updates the status of a specific specification."""

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        spec_id = data.get("spec_id", data.get("task_id", ""))
        new_status = data.get("status", "")
        if not spec_id or not new_status:
            raise ValueError("Requires spec_id and status")
        spring_svc = _spring_svc(sprint_dir)
        spring_id = spring_svc.get_spring_id(sprint_dir)
        spring_svc.ensure_spring_imported(sprint_dir, spring_id)
        specs = spring_svc.get_specs_legacy(spring_id)
        for spec in specs:
            if spec.get("spec_id", spec.get("task_id")) == spec_id:
                spring_svc.update_spec_status(spring_id, spec_id, new_status)
                return {"spec_id": spec_id, "status": new_status}
        raise ValueError(f"Spec {spec_id} not found in delta")
