"""application/services/delta_query_service.py — DeltaQueryService.

Application Service (fachada pública) de CONSULTA sobre el DeltaGraph
(ADR-WALKER-048).

Envuelve los Use Cases de consulta (`use_cases/query_delta.py` y
`use_cases/progress.py`), que son implementación interna del módulo
(INV-013). Consumidores externos (TaskIntelligenceService, kg tasks)
consumen ESTE servicio, nunca los Use Cases directos.

Recibe el DeltaRepository inyectado (puerto), igual que los use cases.
"""
from __future__ import annotations

from typing import Any


class DeltaQueryService:
    """Fachada pública de consultas sobre springs y specs del delta."""

    def __init__(self, repo: Any):
        self._repo = repo

    # ── Delegación en QueryDeltaUseCase ───────────────────────────────

    def get_spring(self, spring_id: str):
        from ..use_cases.query_delta import QueryDeltaUseCase

        return QueryDeltaUseCase(self._repo).get_spring(spring_id)

    def get_active_spring(self):
        from ..use_cases.query_delta import QueryDeltaUseCase

        return QueryDeltaUseCase(self._repo).get_active_spring()

    def list_springs(self, status: str = ""):
        from ..use_cases.query_delta import QueryDeltaUseCase

        return QueryDeltaUseCase(self._repo).list_springs(status)

    def get_specs(self, spring_id: str, status: str = ""):
        from ..use_cases.query_delta import QueryDeltaUseCase

        return QueryDeltaUseCase(self._repo).get_specs(spring_id, status)

    def get_spec(self, spring_id: str, spec_id: str):
        from ..use_cases.query_delta import QueryDeltaUseCase

        return QueryDeltaUseCase(self._repo).get_spec(spring_id, spec_id)

    def get_current_spec(self, spring_id: str):
        from ..use_cases.query_delta import QueryDeltaUseCase

        return QueryDeltaUseCase(self._repo).get_current_spec(spring_id)

    # ── Delegación en use cases de agregación/progreso ────────────────

    def get_sprint_progress(self, spring_id: str) -> dict:
        from ..use_cases.progress import GetSprintProgressUseCase

        return GetSprintProgressUseCase(self._repo).execute(spring_id)

    def get_task_context(self, spring_id: str, spec_id: str) -> dict | None:
        from ..use_cases.progress import GetTaskContextUseCase

        return GetTaskContextUseCase(self._repo).execute(spring_id, spec_id)

    def list_completed_tasks(self, spring_id: str) -> list[dict]:
        from ..use_cases.progress import ListCompletedTasksUseCase

        return ListCompletedTasksUseCase(self._repo).execute(spring_id)
