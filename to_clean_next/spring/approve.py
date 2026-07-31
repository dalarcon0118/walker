"""spring/approve.py — approve-task: mark task as completed in sprint-state.json."""

import json
import os


def cmd_approve_task(args: list[str]) -> int:
    if len(args) < 2:
        print("Uso: spring.py approve-task <chain_dir> <task_id>")
        return 2
    chain_dir = os.path.abspath(args[0])
    task_id = args[1]
    state_path = os.path.join(chain_dir, "sprint-state.json")
    if not os.path.exists(state_path):
        print(f"❌ Sprint no encontrado en {chain_dir}")
        return 2
    with open(state_path) as f:
        state = json.load(f)
    # Update task status in progress.task_states
    progress = state.setdefault("progress", {})
    current = progress.setdefault("current", {})
    task_states = current.setdefault("task_states", {})
    if task_id not in task_states:
        print(f"❌ Tarea {task_id} no encontrada en sprint-state.json")
        return 2
    task_states[task_id] = "completed"
    # Sync top-level tasks dict (legacy compat para spring.py status)
    state.setdefault("tasks", {}).setdefault(task_id, {})["status"] = "completed"
    # Also update context.task_id to next pending task
    state.setdefault("context", {})["last_reason"] = f"PO approved {task_id}"
    # Add audit trail
    trail = state.setdefault("audit", {}).setdefault("trail", [])
    from datetime import datetime, timezone
    trail.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": f"{task_id} marcada como 'completed' via approve-task",
    })
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    print(f"✅ {task_id} approved. Estado actualizado.")
    return 0
