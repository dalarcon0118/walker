"""infrastructure/workflow_store_adapter.py — IWorkflowStore adapter.

Unified adapter that delegates to GraphRepository + StateRepository.
The engine consumes ONLY load_graph/load_state/save_state.
This adapter does NOT expose the repos (avoids "wrapper that re-exposes concretes").
"""
from __future__ import annotations

from typing import Any

from ..domain.ports import IWorkflowStore


class WorkflowStoreAdapter(IWorkflowStore):
    """Unified adapter: delegates to GraphRepository + StateRepository.

    The engine never sees the repos; it only uses the 3 port methods.
    """

    def __init__(self, graph_repo: Any, state_repo: Any):
        self._graph_repo = graph_repo
        self._state_repo = state_repo

    def load_graph(self) -> Any:
        return self._graph_repo.load()

    def load_state(self, chain_dir: str) -> Any:
        return self._state_repo.load(chain_dir)

    def save_state(self, chain_dir: str, state: Any) -> Any:
        return self._state_repo.save(chain_dir, state)
