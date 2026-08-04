"""application/use_cases/query_delta.py — QueryDeltaUseCase.

SRP: consultas de negocio sobre springs y specs del DeltaGraph.
"""
from __future__ import annotations

from features.delta_tracking.domain.entities import Spring, Spec
from features.delta_tracking.application.ports.delta_repository import DeltaRepository


class QueryDeltaUseCase:
    """Consultas de negocio sobre el DeltaGraph."""

    def __init__(self, repo: DeltaRepository):
        self._repo = repo

    def get_spring(self, spring_id: str) -> Spring | None:
        return self._repo.get_spring(spring_id)

    def get_active_spring(self) -> Spring | None:
        return self._repo.get_active_spring()

    def list_springs(self, status: str = "") -> list[Spring]:
        return self._repo.list_springs(status)

    def get_specs(self, spring_id: str, status: str = "") -> list[Spec]:
        return self._repo.get_specs(spring_id, status)

    def get_spec(self, spring_id: str, spec_id: str) -> Spec | None:
        return self._repo.get_spec(spring_id, spec_id)

    def find_spec_by_objective(self, spring_id: str, objective: str) -> Spec | None:
        target = objective.strip()
        if not target:
            return None
        for spec in self._repo.get_specs(spring_id):
            if spec.objective.strip() == target:
                return spec
        return None

    def get_current_spec(self, spring_id: str) -> Spec | None:
        specs = self._repo.get_specs(spring_id)
        in_progress = [s for s in specs if s.status == "in_progress"]
        if in_progress:
            return in_progress[0]
        incomplete = [s for s in specs if s.status not in ("completed", "rejected", "backlog")]
        return incomplete[0] if incomplete else (specs[0] if specs else None)
