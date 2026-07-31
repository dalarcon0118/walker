"""state.py — Sprint state helpers (PROXY).

PROXY MODULE: Re-exports from _walker/workflow_engine/domain/state.py for
backward compat. New code should import directly from _walker module.

Contains legacy migration logic (load_state, save_state, _migrate_from_old)
that bridges old dict-based state to the new SprintState model.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

# ── Re-export from _walker/workflow_engine/domain/state.py ──────────────
from _walker.workflow_engine.domain.state import (
    _load_json,
)

# ── Paths ─────────────────────────────────────────────────────

_STATE_FILE = "sprint-state.json"
_OLD_PROGRESS = "sprint-state.json"
_OLD_WALKER = ".delta-walker.json"


# ── Schema defaults ────────────────────────────────────────────


def _default_state(chain_dir: str | None = None) -> dict:
    """Return a fresh sprint state."""
    return {
        "version": "1.0",
        "sprint": {
            "chain_id": os.path.basename(chain_dir) if chain_dir else "",
            "goal": "",
            "status": "active",
        },
        "tasks": {},
        "task_order": [],
        "walker": {
            "current_node": "SPRINT",
            "current_sub_state": "INIT",
            "sub_state_index": 0,
            "history": ["SPRINT.INIT"],
            "sub_state_data": {},
            "decision_log": [],
            "context": {
                "chain_dir": chain_dir,
                "task_id": None,
            },
        },
        "audit": {
            "trail": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        "unregistered_work": [],
    }


# ── Read / Write ────────────────────────────────────────────────


def load_state(chain_dir: str | None = None) -> dict:
    """Load sprint state, migrating from old format if needed."""
    if not chain_dir:
        return _default_state()

    state_path = os.path.join(chain_dir, _STATE_FILE)
    if os.path.exists(state_path):
        raw = _load_json(state_path)
        if raw:
            # SSOT: progress.current.task_states tiene los status reales
            real_statuses = (
                raw.get("progress", {}).get("current", {}).get("task_states", {})
            )
            # Auto-normalize: tasks → dict format
            raw_tasks = raw.get("tasks", {})
            if isinstance(raw_tasks, list):
                raw["tasks"] = {
                    tid: {
                        "status": real_statuses.get(tid, "pending")
                        if isinstance(real_statuses.get(tid), str)
                        else real_statuses.get(tid, {}).get("status", "pending")
                    }
                    for tid in raw_tasks
                }
            elif isinstance(raw_tasks, dict):
                for tid, tdata in list(raw_tasks.items()):
                    if not isinstance(tdata, dict):
                        raw["tasks"][tid] = {"status": str(tdata)}
            return raw

    # Migration from old format
    migrated = _migrate_from_old(chain_dir)
    if migrated:
        return migrated

    return _default_state(chain_dir)


def save_state(state: dict, chain_dir: str | None = None) -> None:
    """Persist sprint state to disk."""
    if not chain_dir:
        return
    state["audit"]["updated_at"] = datetime.now(timezone.utc).isoformat()
    state_path = os.path.join(chain_dir, _STATE_FILE)
    try:
        with open(state_path, "w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"  [error] Error guardando sprint-state.json: {e}")


# ── Migration ──────────────────────────────────────────────────


def _migrate_from_old(chain_dir: str) -> dict | None:
    """Migrate from sprint-state.json + .delta-walker.json to sprint-state.json.

    Returns the new state dict if migration was possible, None otherwise.
    """
    sprint_state_path = os.path.join(chain_dir, _OLD_PROGRESS)
    walker_path = os.path.join(chain_dir, _OLD_WALKER)
    plan_path = os.path.join(chain_dir, "plan.json")

    prog = _load_json(sprint_state_path)
    walker = _load_json(walker_path)
    plan = _load_json(plan_path)

    if not prog and not walker:
        return None  # Nothing to migrate

    state = _default_state(chain_dir)

    # Migrate walker state
    if walker:
        state["walker"]["current_node"] = walker.get("current_node", "SPRINT")
        state["walker"]["current_sub_state"] = walker.get("current_sub_state", "INIT")
        state["walker"]["sub_state_index"] = walker.get("sub_state_index", 0)
        state["walker"]["history"] = walker.get("history", [])
        state["walker"]["sub_state_data"] = walker.get("sub_state_data", {})
        state["walker"]["decision_log"] = walker.get("decision_log", [])
        state["walker"]["context"] = walker.get("context", {})
        # Also copy from sprint-state.json walker_data if present
        if prog:
            wd = prog.get("progress", prog).get("walker_data", {})
            if wd.get("decision_log"):
                state["walker"]["decision_log"] = wd["decision_log"]

    # Migrate task states from sprint-state.json
    if prog:
        p = prog.get("progress", prog)
        state["sprint"]["goal"] = p.get("meta", {}).get("description", "")
        state["sprint"]["status"] = p.get("meta", {}).get("status", "active")
        state["sprint"]["chain_id"] = p.get("meta", {}).get("chain_id", "")

        # Migrate trail
        state["audit"]["trail"] = p.get("trail", [])

        # Migrate task states (SSOT: progress.current.task_states)
        real_statuses = p.get("current", {}).get("task_states", {})
        ts_fallback = prog.get("tasks", {})
        if isinstance(ts_fallback, list):
            ts_fallback = {}
        ts = real_statuses or ts_fallback
        if isinstance(ts, dict):
            for tid, status in ts.items():
                s = status.get("status", status) if isinstance(status, dict) else status
                state["tasks"][tid] = {"status": s}
        elif isinstance(ts, list):
            for tid in ts:
                state["tasks"][tid] = {"status": "pending"}

        # Migrate contract_data
        cd = p.get("contract_data", {})
        if cd:
            state["walker"]["contract_data"] = cd

        # Migrate po_approved
        po = p.get("po_approved", {})
        if po:
            for tid, approved in po.items():
                if tid not in state["tasks"]:
                    state["tasks"][tid] = {"status": "pending"}
                state["tasks"][tid]["po_approved"] = approved

    # Enrich with plan.json data (result_state, predicates)
    if plan:
        pl = plan.get("plan", plan)
        task_order = []
        for t in pl.get("tasks", []):
            tid = t.get("task_id", "")
            if not tid:
                continue
            task_order.append(tid)
            if tid not in state["tasks"]:
                state["tasks"][tid] = {"status": "pending"}
            if t.get("result_state"):
                state["tasks"][tid]["result_state"] = t["result_state"]
            preds = t.get("validation", {}).get("predicates", [])
            if preds:
                state["tasks"][tid]["predicates"] = {"ok": 0, "total": len(preds)}
        state["task_order"] = task_order

    # Save new state
    save_state(state, chain_dir)

    # Archive old files (rename to .bak)
    for old_path in [sprint_state_path, walker_path]:
        if os.path.exists(old_path):
            bak = old_path + ".bak"
            try:
                os.rename(old_path, bak)
                print(
                    f"  [migrate] {os.path.basename(old_path)} → {os.path.basename(bak)}"
                )
            except OSError:
                pass

    print(f"  ✅ [migrate] sprint-state.json creado desde {len(state['tasks'])} tareas")
    return state


# ── High-level helpers (delegados a _walker) ────────────────────


def register_unregistered_work(
    state: dict, file_path: str, description: str = "", current_task: str = ""
) -> str | None:
    """Register work on a file not covered by any task. Returns the UW id or None.

    Si la tarea actual existe y el archivo esta dentro de sus artefactos,
    retorna None (trabajo registrado). Si no, crea una entrada UW.
    """
    # Check if file is within scope of current task
    if current_task:
        task = state.get("tasks", {}).get(current_task, {})
        task_files = task.get("impacted_artifacts", [])
        if isinstance(task_files, list) and file_path in task_files:
            return None  # Already in scope

    # Check if already registered
    uw_list: list[dict] = state.setdefault("unregistered_work", [])
    for uw in uw_list:
        if uw.get("file") == file_path and uw.get("status") == "pending_registration":
            return uw.get("id")  # Already registered, return existing ID

    # Create new UW entry
    uw_id = f"UW-{len(uw_list) + 1:03d}"
    entry = {
        "id": uw_id,
        "file": file_path,
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "description": description or f"Trabajo en {file_path} fuera del sprint",
        "status": "pending_registration",
        "current_task": current_task or "none",
    }
    uw_list.append(entry)
    return uw_id


def get_unregistered_work(state: dict) -> list[dict]:
    """Get all pending unregistered work entries."""
    return [
        uw
        for uw in state.get("unregistered_work", [])
        if uw.get("status") == "pending_registration"
    ]


def resolve_unregistered_work(state: dict, uw_id: str, task_id: str) -> bool:
    """Mark UW entry as registered (resolved by creating a task)."""
    for uw in state.get("unregistered_work", []):
        if uw.get("id") == uw_id:
            uw["status"] = "registered"
            uw["task_id"] = task_id
            uw["resolved_at"] = datetime.now(timezone.utc).isoformat()
            return True
    return False
