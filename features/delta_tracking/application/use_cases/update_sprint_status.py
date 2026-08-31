"""use_cases/update_sprint_status.py — UpdateSprintStatusUseCase.

Actualiza el status del sprint: planning → active → reviewing → closed.
PRINCIPIO: el estado del spring vive en el delta. Mutar el status del
nodo spring via SpringService (fuente unica). No escribe sprint-state.json.
"""
from __future__ import annotations

from shared.core.engine.domain.execution import ExecutionContext


class UpdateSprintStatusUseCase:
    """Updates sprint status: planning → active → reviewing → closed."""

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        new_status = data.get("status", "")
        if not new_status:
            raise ValueError("Requires --status")
        from features.delta_tracking import spring_service

        spring_svc = spring_service(chain_dir=sprint_dir)
        if not spring_svc:
            raise ValueError("cannot resolve SpringService")
        spring_id = spring_svc.get_spring_id(sprint_dir)
        spring = spring_svc.ensure_spring_imported(sprint_dir, spring_id)
        if not spring:
            raise ValueError(f"Spring '{spring_id}' not found in delta")
        current = spring.get("status", "planning")
        from ...domain.services.spec_service import SprintStatusUpdater

        allowed, reason = SprintStatusUpdater.can_transition(current, new_status)
        if not allowed:
            raise ValueError(reason)
        spring_svc.set_spring_status(spring_id, new_status)
        return {"previous_status": current, "status": new_status}
