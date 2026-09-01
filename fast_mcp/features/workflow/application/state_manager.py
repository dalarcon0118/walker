"""application/state_manager.py — Thin helpers for WorkflowState persistence.

Original: 127 LOC with contract_data, decision_log, apply_state_transition.
Simplified: 30 LOC with 2 helpers. All domain-specific logic deferred.
"""
from __future__ import annotations

from typing import Any

from ..domain.entities import WorkflowState
from ..domain.ports import IWorkflowStore


def ensure_state(chain_dir: str, store: IWorkflowStore) -> dict:
    """Load state as dict. Raises ValueError if chain_dir empty.

    PRINCIPLE: no invented defaults. Empty state only if nothing is persisted.
    """
    if not chain_dir:
        raise ValueError("chain_dir is empty — cannot resolve state")
    state = store.load_state(chain_dir)
    return state.to_dict()


def store_submit_data(
    chain_dir: str,
    sub_key: str,
    fields: dict,
    store: IWorkflowStore,
) -> None:
    """Persist submit data to WorkflowState.sub_state_data."""
    if not chain_dir or not fields:
        return
    state = store.load_state(chain_dir)
    state = state.record_submit(sub_key, fields)
    store.save_state(chain_dir, state)
