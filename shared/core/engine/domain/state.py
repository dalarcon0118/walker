"""state.py — Sprint state helpers for the workflow engine.

SSOT for state management utilities migrated from scripts/state.py.
The core WorkflowState entity lives in models.py; this module contains
the helper functions that operate on it and bridge with legacy dict state.
"""

from __future__ import annotations

import json

from .models import WorkflowState
from .ports_registry import resolve_port
from .ports import IStateStore
from returns.result import Failure, Result, Success


# ── JSON helpers ────────────────────────────────────────────────────────────


def _load_json(path: str) -> dict | None:
    """Load a JSON file safely. Returns None on failure."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# ── WorkflowState repository bridge ───────────────────────────────────────────


def load_state_from_repo(chain_dir: str) -> Result[WorkflowState, str]:
    """Load WorkflowState via the IStateStore port.

    Devuelve un WorkflowState por defecto si el archivo no existe (comportamiento
    del adapter WorkflowStateRepository). Si el puerto no esta registrado, retorna
    Failure (fail-fast explicito en vez de import infra directo).

    Returns:
        Result[WorkflowState, str]: Success(state) o Failure(motivo).
    """
    store = resolve_port(IStateStore)
    if store is None:
        return Failure("state_store_not_registered")
    try:
        return Success(store.load(chain_dir))
    except Exception:
        return Failure("no_state")


def save_state_to_repo(chain_dir: str, state: WorkflowState) -> Result[None, str]:
    """Persist WorkflowState via the IStateStore port.

    Returns:
        Result[None, str]: Success(None) o Failure(motivo).
    """
    store = resolve_port(IStateStore)
    if store is None:
        return Failure("state_store_not_registered")
    try:
        store.save(chain_dir, state)
        return Success(None)
    except Exception:
        return Failure("save_failed")


# ── WorkflowState ↔ dict conversion helpers ───────────────────────────────────


def sprint_state_to_dict(state: WorkflowState) -> dict:
    """Convert WorkflowState to a plain dict (for legacy consumers)."""
    return state.to_dict()


def dict_to_sprint_state(data: dict) -> WorkflowState:
    """Convert a plain dict to WorkflowState."""
    return WorkflowState.from_dict(data)
