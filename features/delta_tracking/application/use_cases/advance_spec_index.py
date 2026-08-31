"""use_cases/advance_spec_index.py — AdvanceSpecIndexUseCase.

Mueve al siguiente índice de spec del spring.
"""
from __future__ import annotations

from shared.core.engine.domain.execution import ExecutionContext
from ._sprint_helpers import _get_current_spec_index


class AdvanceSpecIndexUseCase:
    """Moves to the next specification index."""

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        if not sprint_dir:
            raise ValueError("chain_dir not available")
        current_idx = _get_current_spec_index(sprint_dir)
        new_idx = current_idx + 1
        # SSOT: el contexto de negocio vive en el delta (nodo spring)
        from features.delta_tracking import spring_service

        spring_svc = spring_service(chain_dir=sprint_dir)
        if spring_svc:
            spring_id = spring_svc.get_spring_id(sprint_dir)
            spring_svc.ensure_spring_imported(sprint_dir, spring_id)
            spring_svc.set_spring_context(
                spring_id, current_spec_index=new_idx, current_task_index=new_idx
            )
        return {"previous_index": current_idx, "current_index": new_idx}
