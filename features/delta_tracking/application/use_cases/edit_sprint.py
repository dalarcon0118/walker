"""use_cases/edit_sprint.py — EditSprintUseCase.

Edita un sprint existente: agrega/actualiza specs sin sobrescribir.
Unlike PlanSprintUseCase (which creates from scratch), this merges
changes: adds new specs, updates the goal, preserves existing specs.
"""
from __future__ import annotations

from shared.common.shared_domain import ExecutionContext
from ._sprint_helpers import _spring_svc


class EditSprintUseCase:
    """Edits an existing sprint: adds/updates specs without overwriting."""

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        spring_svc = _spring_svc(sprint_dir)
        spring_id = spring_svc.get_spring_id(sprint_dir)
        spring_svc.ensure_spring_imported(sprint_dir, spring_id)
        spring_svc.get_spring(spring_id) or {}
        specs = spring_svc.get_specs_legacy(spring_id)

        # Update goal if provided
        if data.get("goal"):
            spring_svc.update_spring(spring_id, {"goal": data["goal"]})

        # Add new specs from planning input (upsert al delta)
        specs_raw = data.get("tasks", data.get("specifications", []))
        if specs_raw:
            items = [
                {
                    "objective": item.get("objective", ""),
                    "domain": item.get("domain", ""),
                    "type": item.get("type", "feature"),
                    "result_state": item.get("result_state", ""),
                    "files": (
                        item.get("impacted_artifacts", {}).get("files", [])
                        if isinstance(item.get("impacted_artifacts"), dict)
                        else []
                    ),
                    "status": item.get("status", "draft"),
                    "description": item.get("task_description", ""),
                }
                for item in specs_raw
            ]
            spring_svc.upsert_specs_batch(spring_id, items)

        # Update existing spec if spec_id matches
        spec_updates = data.get("spec_updates", [])
        for update in spec_updates:
            update_id = update.get("spec_id", update.get("task_id", ""))
            for s in specs:
                if s.get("spec_id", s.get("task_id", "")) == update_id:
                    spring_svc.update_spec(spring_id, update_id, update)
                    break

        updated_specs = spring_svc.get_specs_legacy(spring_id)
        goal = spring_svc.get_spring(spring_id) or {}
        return {
            "modified": True,
            "spec_count": len(updated_specs),
            "goal": goal.get("goal", ""),
        }
