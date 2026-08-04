"""application/use_cases/import_legacy.py — ImportLegacySprintUseCase.

SRP: importa un sprint legacy (plan.json + workflow state) al DeltaGraph.
"""
from __future__ import annotations

import json
import os

from features.delta_tracking.domain.entities import Spring, Spec
from features.delta_tracking.domain.values import EDGE_CONTAINS
from features.delta_tracking.application.ports.delta_repository import DeltaRepository


SPRING_STATUS_FLOW = ["planning", "active", "reviewing", "closed"]


class ImportLegacySprintUseCase:
    """Importa un sprint legacy al DeltaGraph."""

    def __init__(self, repo: DeltaRepository):
        self._repo = repo

    def execute(self, sprint_dir: str) -> dict:
        plan_path = os.path.join(sprint_dir, "plan.json")
        if not os.path.exists(plan_path):
            return {"imported": False, "spring_id": "", "specs": 0}

        with open(plan_path) as f:
            plan = json.load(f)

        spring_id = plan.get("chain_id") or os.path.basename(sprint_dir)
        goal = plan.get("goal", "")

        # Estado desde workflow state o plan.json
        status = "planning"
        wf_state_path = os.path.join(sprint_dir, ".workflow-state.json")
        if os.path.exists(wf_state_path):
            try:
                with open(wf_state_path) as f:
                    wf_state = json.load(f)
                sprint_status = wf_state.get("sprint", {}).get("status", "")
                if sprint_status in SPRING_STATUS_FLOW:
                    status = sprint_status
            except Exception:
                pass

        # Crear spring
        spring = Spring(
            spring_id=spring_id,
            goal=goal,
            status=status,
            active=(status == "active"),
        )
        self._repo.save_spring(spring)

        # Importar specs en batch
        specs = plan.get("specifications", plan.get("tasks", []))
        batch: list[Spec] = []
        for i, item in enumerate(specs):
            batch.append(Spec(
                spring_id=spring_id,
                objective=item.get("objective", ""),
                domain=item.get("domain", ""),
                spec_type=item.get("type", "feature"),
                spec_index=i,
                result_state=item.get("result_state", ""),
                files=(
                    item.get("impacted_artifacts", {}).get("files", [])
                    if isinstance(item.get("impacted_artifacts"), dict) else []
                ),
                status=item.get("status", "draft"),
                description=item.get("task_description", ""),
            ))

        if batch:
            self._repo.save_specs_batch(batch)
            for spec in batch:
                self._repo.add_edge(spring_id, spec.doc_id, EDGE_CONTAINS)

        return {"imported": True, "spring_id": spring_id, "specs": len(specs)}
