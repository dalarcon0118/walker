"""use_cases/show_dashboard.py — ShowDashboardUseCase.

Devuelve el dashboard del sprint. PRINCIPIO: el estado del spring (goal,
specs, estados) vive en el DELTA (SpringService), fuente unica.
"""
from __future__ import annotations

from shared.common.shared_domain import ExecutionContext


class ShowDashboardUseCase:
    """Returns the current sprint dashboard state."""

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        if not sprint_dir:
            return {
                "sprint_id": "",
                "goal": "",
                "specs": [],
                "spec_count": 0,
                "completed_count": 0,
            }
        try:
            from features.delta_tracking import spring_service

            spring_svc = spring_service(chain_dir=sprint_dir)
            if not spring_svc:
                return {
                    "sprint_id": "",
                    "goal": "",
                    "specs": [],
                    "spec_count": 0,
                    "completed_count": 0,
                }
            spring_id = spring_svc.get_spring_id(sprint_dir)
            spring = spring_svc.ensure_spring_imported(sprint_dir, spring_id)
            spring_data = spring or {}
            specs = spring_svc.get_specs_legacy(spring_id)
        except Exception:
            return {
                "sprint_id": "",
                "goal": "",
                "specs": [],
                "spec_count": 0,
                "completed_count": 0,
            }
        goal = spring_data.get("goal", "")
        total = len(specs)
        completed = sum(
            1 for s in specs if isinstance(s, dict) and s.get("status") == "completed"
        )
        return {
            "sprint_id": spring_data.get("_id", "") or spring_id,
            "goal": goal,
            "specs": specs,
            "spec_count": total,
            "task_count": total,
            "completed_count": completed,
        }
