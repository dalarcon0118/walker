"""application/use_cases/progress.py — Use cases de agregacion del DeltaGraph.

SRP: consultas de progreso y contexto sobre springs y specs.
"""
from __future__ import annotations

from features.delta_tracking.application.ports.delta_repository import DeltaRepository
from features.delta_tracking.domain.entities import Spec


class GetSprintProgressUseCase:
    """Agregado del progreso de un sprint: specs por status + stats."""

    def __init__(self, repo: DeltaRepository):
        self._repo = repo

    def execute(self, spring_id: str) -> dict:
        spring = self._repo.get_spring(spring_id)
        specs = self._repo.get_specs(spring_id)

        by_status: dict[str, int] = {}
        for s in specs:
            by_status[s.status] = by_status.get(s.status, 0) + 1

        total = len(specs)
        completed = by_status.get("completed", 0)
        in_progress = by_status.get("in_progress", 0)
        remaining = total - completed - by_status.get("rejected", 0) - by_status.get("backlog", 0)

        return {
            "spring_id": spring_id,
            "spring_status": spring.status if spring else "unknown",
            "total_specs": total,
            "completed": completed,
            "in_progress": in_progress,
            "remaining": remaining,
            "by_status": by_status,
            "progress_pct": round(completed / total * 100, 1) if total > 0 else 0,
        }


class GetTaskContextUseCase:
    """Contexto completo de una tarea: spec + estado + archivos + dependencias."""

    def __init__(self, repo: DeltaRepository):
        self._repo = repo

    def execute(self, spring_id: str, spec_id: str) -> dict | None:
        spec = self._repo.get_spec(spring_id, spec_id)
        if spec is None:
            return None

        spring = self._repo.get_spring(spring_id)

        # Resolver dependencias
        deps = []
        for dep_id in spec.depends_on:
            dep_spec = self._repo.get_spec(spring_id, dep_id)
            if dep_spec:
                deps.append({
                    "spec_id": dep_spec.spec_id,
                    "objective": dep_spec.objective,
                    "status": dep_spec.status,
                })

        return {
            "spec_id": spec.spec_id,
            "spec_key": spec.doc_id,
            "objective": spec.objective,
            "domain": spec.domain,
            "status": spec.status,
            "result_state": spec.result_state,
            "description": spec.description,
            "spec_type": spec.spec_type,
            "files": spec.files,
            "dependencies": deps,
            "depends_on": spec.depends_on or [],
            "spring_status": spring.status if spring else "unknown",
            "spring_goal": spring.goal if spring else "",
            "updated_at": spec.updated_at,
        }


class ListCompletedTasksUseCase:
    """Tareas completadas del sprint (para retrospectiva y review)."""

    def __init__(self, repo: DeltaRepository):
        self._repo = repo

    def execute(self, spring_id: str) -> list[dict]:
        specs = self._repo.get_specs(spring_id)
        completed = [s for s in specs if s.status == "completed"]
        return [
            {
                "spec_id": s.spec_id,
                "objective": s.objective,
                "domain": s.domain,
                "files": s.files,
                "result_state": s.result_state,
                "updated_at": s.updated_at,
            }
            for s in sorted(completed, key=lambda s: s.spec_index)
        ]
