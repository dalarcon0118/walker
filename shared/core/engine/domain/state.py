"""state.py — Sprint state helpers for the workflow engine.

SSOT for state management utilities migrated from scripts/state.py.
The core WorkflowState entity lives in models.py; this module contains
the helper functions that operate on it and bridge with legacy dict state.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .models import WorkflowState


# ── JSON helpers ────────────────────────────────────────────────────────────


def _load_json(path: str) -> dict | None:
    """Load a JSON file safely. Returns None on failure."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# ── WorkflowState repository bridge ───────────────────────────────────────────


def load_state_from_repo(chain_dir: str) -> WorkflowState:
    """Load WorkflowState using the infrastructure repository.

    Returns a default WorkflowState if the file doesn't exist.
    """
    from ..infrastructure.repositories import WorkflowStateRepository

    repo = WorkflowStateRepository()
    return repo.load(chain_dir)


def save_state_to_repo(chain_dir: str, state: WorkflowState) -> None:
    """Persist WorkflowState using the infrastructure repository."""
    from ..infrastructure.repositories import WorkflowStateRepository

    repo = WorkflowStateRepository()
    repo.save(chain_dir, state)


# ── Task status helpers (operate on dict state for backward compat) ─────────


def get_task_status(state: dict, task_id: str) -> str:
    """Get task status from consolidated state dict."""
    return state.get("tasks", {}).get(task_id, {}).get("status", "unknown")


def set_task_status(state: dict, task_id: str, status: str) -> None:
    """Update task status in consolidated state dict.

    SSOT: progress.current.task_states is the source of truth.
    Top-level tasks dict is updated as mirror for legacy compatibility.
    """
    progress = state.setdefault("progress", {})
    current = progress.setdefault("current", {})
    task_states = current.setdefault("task_states", {})
    task_states[task_id] = status

    # Mirror: top-level tasks (legacy compat)
    if task_id not in state.get("tasks", {}):
        state.setdefault("tasks", {})[task_id] = {}
    state["tasks"][task_id]["status"] = status

    # Audit trail entry
    state.setdefault("audit", {}).setdefault("trail", []).append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": f"{task_id} marked as '{status}' via state.py",
        }
    )


def complete_task(state: dict, task_id: str) -> None:
    """Mark a task as completed.

    ANTI-ALUCINACIÓN (G-W6): completar una task NO satisface sus predicates.
    Antes este helper hacía ``predicates["ok"] = total`` (100% ok sin
    verificar nada) — un bypass total de VERIFY.PREDICATE_CHECK. La
    verificación real ocurre en verify_predicates (motor con ast-grep /
    subprocess). Si un caller quiere satisfacer predicates, debe correr el
    gate, nunca marcarlos a mano.
    """
    set_task_status(state, task_id, "completed")


# ── WorkflowState ↔ dict conversion helpers ───────────────────────────────────


def sprint_state_to_dict(state: WorkflowState) -> dict:
    """Convert WorkflowState to a plain dict (for legacy consumers)."""
    return state.to_dict()


def dict_to_sprint_state(data: dict) -> WorkflowState:
    """Convert a plain dict to WorkflowState."""
    return WorkflowState.from_dict(data)
