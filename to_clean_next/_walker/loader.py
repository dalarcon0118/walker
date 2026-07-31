"""
loader.py — Carga de workflow y estado desde disco.

SRP: Unica responsabilidad = leer/interpretar/recuperar workflow.json y sprint-state.json.
Extraido de engine.py para separar IO del resto de la logica.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from string import Template

from utils import (
    load_json,
    state_path,
    save_state,
    _DONE_STATUSES,
    _INIT_SCRIPT,
    _resolve_task_id,
)
from .migrate import _migrate_workflow, _emergency_workflow, _presprint_workflow
from .plan_editor import _load_rejected_tasks


def find_workflow(chain_dir: str | None = None) -> dict:
    """Load the project workflow.json or return minimal fallback with instructions."""
    if chain_dir:
        from _walker.shared.path_resolver import resolve_project_root, resolve_project_id

        parent = resolve_project_root(chain_dir)
        workflow_path = os.path.join(parent, "workflow.json")
        if os.path.exists(workflow_path):
            data = load_json(workflow_path)
            if data:
                if _migrate_workflow(workflow_path, data):
                    data = load_json(workflow_path)
                return data

        project_dir = parent
        project_id = resolve_project_id(chain_dir) or "unknown"
        print()
        print("=" * 70)
        print("  ⚠️  WORKFLOW NO ENCONTRADO")
        print("=" * 70)
        print()
        print(
            Template("  No hay workflow.json para el proyecto '$pid'.").substitute(
                pid=project_id
            )
        )
        print("  El workflow define el proceso (5 nodos, sub-estados deterministas).")
        print("  Sin el, walker.py usa un workflow minimo de emergencia.")
        print()
        print("  PARA CREARLO, ejecuta este comando:")
        print()
        print(
            Template("    python3 $script $dir").substitute(
                script=_INIT_SCRIPT, dir=project_dir
            )
        )
        print()
        print("  Esto generara .cognitive/projects/<pid>/workflow.json")
        print("  con el workflow v4 (5 nodos con sub-estados secuenciales).")
        print("  Luego vuelve a ejecutar walker.py.")
        print()
        print("  MIENTRAS TANTO, se usara un workflow minimo de emergencia")
        print("  para que puedas comenzar.")
        print("=" * 70)
        print()
        return _emergency_workflow()
    return _presprint_workflow()


def ensure_state(chain_dir: str | None = None) -> dict:
    """Read walker state from sprint-state.json or reconstruct, or return ephemeral default."""
    if chain_dir:
        sp = state_path(chain_dir)
        if sp and os.path.exists(sp):
            state = load_json(sp)
            if state:
                if not state.get("current_node") and state.get("walker"):
                    state = dict(state.get("walker", {}))
                return state

        reconstructed = _reconstruct_walker_state(chain_dir)
        if reconstructed:
            save_state(reconstructed, chain_dir)
            return reconstructed

    return {
        "current_node": "SPRINT",
        "current_sub_state": "INIT",
        "sub_state_index": 0,
        "history": ["SPRINT.INIT"],
        "sub_state_data": {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "context": {"chain_dir": chain_dir, "task_id": None},
    }


def _reconstruct_walker_state(chain_dir: str) -> dict | None:
    """Reconstruct walker state from sprint-state.json when .delta-walker.json is missing (v4)."""
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")
    if not os.path.exists(sprint_state_path):
        return None

    try:
        with open(sprint_state_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return None

    prog = data.get("progress", data)
    current = prog
    task_states = current.get("task_states", {})
    finished = current.get("finished", False)

    total_tasks = len(task_states)
    completed_tasks = sum(
        1
        for s in task_states.values()
        if isinstance(s, str) and s.lower() in _DONE_STATUSES
    )
    has_pending = total_tasks > completed_tasks

    if finished or (completed_tasks == total_tasks and total_tasks > 0):
        node, sub_state, idx = "SPRINT", "CLOSE_CHECK", 5
    elif has_pending:
        node, sub_state, idx = "SPRINT", "ACTIVE", 3
    else:
        node, sub_state, idx = "SPRINT", "INIT", 0

    history = [f"{node}.{sub_state}"]
    rejected = _load_rejected_tasks(chain_dir)
    tid = _resolve_task_id(chain_dir, rejected)
    state = {
        "current_node": node,
        "current_sub_state": sub_state,
        "sub_state_index": idx,
        "history": history,
        "sub_state_data": {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "context": {
            "chain_dir": chain_dir,
            "task_id": tid,
            "reconstructed": True,
            "reconstructed_from": "sprint-state.json",
            "rejected_tasks": list(rejected),
        },
    }
    return state
