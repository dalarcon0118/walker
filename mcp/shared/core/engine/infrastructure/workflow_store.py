"""
infrastructure/workflow_store.py — Adapter del puerto IWorkflowStore.

UNIFICACIÓN REAL del acceso al workflow (grafo + estado) en UN puerto
(ADR-WALKER-067 §7.2). El host construye este adapter UNA vez con los repos
concretos; el engine consume SOLO `load_graph`/`load_state`/`save_state`.
Este adapter NO expone los repos (evita el anti-patrón "wrapper que re-expone
concretos").
"""
from __future__ import annotations

from typing import Any

from shared.core.engine.domain.ports import IWorkflowStore
from shared.core.engine.infrastructure.repositories import (
    WorkflowRepository,
    WorkflowStateRepository,
)


class WorkflowStoreAdapter(IWorkflowStore):
    """Adapter que expone grafo + estado detrás de un solo puerto."""

    def __init__(
        self,
        workflow_repo: WorkflowRepository | None = None,
        state_repo: WorkflowStateRepository | None = None,
    ):
        self._workflow_repo = workflow_repo or WorkflowRepository()
        self._state_repo = state_repo or WorkflowStateRepository()

    def load_graph(self) -> Any:
        return self._workflow_repo.load()

    def load_state(self, chain_dir: str) -> Any:
        return self._state_repo.load(chain_dir)

    def save_state(self, state: Any, chain_dir: str) -> Any:
        # Retorna el estado persistido (nueva revisión CAS) para encadenar.
        return self._state_repo.save(chain_dir, state)