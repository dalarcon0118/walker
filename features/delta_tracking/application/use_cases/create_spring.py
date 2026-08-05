"""application/use_cases/create_spring.py — CreateSpringUseCase.

SRP: crea un spring en el DeltaGraph con estado inicial.
"""
from __future__ import annotations

from features.delta_tracking.domain.entities import Spring
from features.delta_tracking.application.ports.delta_repository import DeltaRepository


class CreateSpringUseCase:
    """Crea un spring nuevo en el DeltaGraph."""

    def __init__(self, repo: DeltaRepository):
        self._repo = repo

    def execute(
        self,
        spring_id: str,
        goal: str = "",
        status: str = "planning",
        project_id: str = "",
        session_id: str = "",
        **extra,
    ) -> Spring:
        spring = Spring(
            spring_id=spring_id,
            goal=goal,
            status=status,
            active=(status == "active"),
            project_id=project_id,
            session_id=session_id,
            **{k: v for k, v in extra.items() if k in Spring.__dataclass_fields__},
        )
        self._repo.save_spring(spring)
        return spring
