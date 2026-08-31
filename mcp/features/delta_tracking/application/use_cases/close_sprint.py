"""use_cases/close_sprint.py — CloseSprintUseCase.

Cierra el sprint actual. PRINCIPIO: el estado del spring vive en el delta.
Cerrar = mutar el nodo spring (status -> closed). No escribe sprint-state.json.
"""
from __future__ import annotations

from shared.core.engine.domain.execution import ExecutionContext


class CloseSprintUseCase:
    """Closes the current sprint."""

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        if not sprint_dir:
            raise ValueError("chain_dir not available")
        from features.delta_tracking import spring_service

        spring_svc = spring_service(chain_dir=sprint_dir)
        if not spring_svc:
            raise ValueError("cannot resolve SpringService")
        spring_id = spring_svc.get_spring_id(sprint_dir)
        spring = spring_svc.ensure_spring_imported(sprint_dir, spring_id)
        if not spring:
            raise ValueError(f"Spring '{spring_id}' not found in delta")
        spring_svc.set_spring_status(spring_id, "closed")
        return {"status": "closed", "sprint_id": spring_id}
