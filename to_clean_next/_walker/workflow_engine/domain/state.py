"""state.py — Sprint state helpers for the workflow engine.

SSOT for state management utilities migrated from scripts/state.py.
The core SprintState entity lives in models.py; this module contains
the helper functions that operate on it and bridge with legacy dict state.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .models import SprintState


# ── JSON helpers ────────────────────────────────────────────────────────────


def _load_json(path: str) -> dict | None:
    """Load a JSON file safely. Returns None on failure."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# ── SprintState repository bridge ───────────────────────────────────────────


def load_state_from_repo(chain_dir: str) -> SprintState:
    """Load SprintState using the infrastructure repository.

    Returns a default SprintState if the file doesn't exist.
    """
    from ..infrastructure.repositories import SprintStateRepository

    repo = SprintStateRepository()
    return repo.load(chain_dir)


def save_state_to_repo(chain_dir: str, state: SprintState) -> None:
    """Persist SprintState using the infrastructure repository."""
    from ..infrastructure.repositories import SprintStateRepository

    repo = SprintStateRepository()
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
    """Mark a task as completed and satisfy all predicates."""
    set_task_status(state, task_id, "completed")
    task = state.get("tasks", {}).get(task_id, {})
    if task.get("predicates") and isinstance(task["predicates"], dict):
        total = task["predicates"].get("total", 0)
        task["predicates"]["ok"] = total


# ── SprintState ↔ dict conversion helpers ───────────────────────────────────


def sprint_state_to_dict(state: SprintState) -> dict:
    """Convert SprintState to a plain dict (for legacy consumers)."""
    return state.to_dict()


def dict_to_sprint_state(data: dict) -> SprintState:
    """Convert a plain dict to SprintState."""
    return SprintState.from_dict(data)
