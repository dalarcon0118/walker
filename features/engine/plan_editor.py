"""
plan_edit — execute plan edit module

Extracted from walker.py.
"""

import os
import json
import sys
import subprocess
from string import Template
from libs.infrastructure.utils import (
    eprint,
    load_json,
    _backlog_path,
    _TASK_DEFAULTS,
    _EDITABLE_FIELDS,
    _SPRING_SCRIPT,
    _SCRIPT_TIMEOUT,
)
from datetime import datetime, timezone


_LLM_REQUIRED_FIELDS = {
    "objective",
    "result_state",
    "type",
    "domain",
    "work_type",
    "role",
    "scrum_role",
}


def _build_add_task_cmd(chain_dir: str, flags: dict[str, str]) -> list[str] | None:
    """Construye el comando add-task (valida campos requeridos del LLM)."""
    missing = [f for f in _LLM_REQUIRED_FIELDS if not flags.get(f)]
    if missing:
        eprint(
            Template("  [edit_plan] Faltan campos requeridos: $fields").substitute(
                fields=", ".join(missing)
            )
        )
        eprint("  Uso: walker.py transition plan_updated --action add-task \\")
        eprint(
            '       --objective "..." --result_state "..." --type feature --domain tooling \\'
        )
        eprint("       --work_type refactor --role core --scrum_role dev")
        return None
    task_data = dict(_TASK_DEFAULTS)
    task_data.update({f: flags[f] for f in _LLM_REQUIRED_FIELDS})
    task_data["task_id"] = flags.get("task_id", "__pending__")
    # Pass through all other user-provided fields (override defaults)
    passthrough = (
        _EDITABLE_FIELDS - _LLM_REQUIRED_FIELDS - {"action", "task_id", "field", "value"}
    )
    for f in passthrough:
        if f in flags and flags[f]:
            try:
                task_data[f] = (
                    json.loads(flags[f])
                    if flags[f].startswith(("[", "{"))
                    else flags[f]
                )
            except (json.JSONDecodeError, ValueError):
                task_data[f] = flags[f]
    # Convert numeric flags
    if "importer_files" in flags:
        task_data["importer_files"] = int(flags["importer_files"])
    task_json = json.dumps(task_data)
    return [
        sys.executable,
        _SPRING_SCRIPT,
        "add-task",
        chain_dir,
        "--task",
        task_json,
    ]


def _build_update_task_cmd(
    chain_dir: str, task_id: str | None, flags: dict[str, str]
) -> list[str] | None:
    """Construye el comando update-task (valida campo editable y existencia)."""
    tid = flags.get("task_id", task_id or "")
    field = flags.get("field", "")
    value = flags.get("value", "")
    if not tid or not field:
        eprint("  [edit_plan] --task_id y --field son requeridos para update-task")
        return None
    if field not in _EDITABLE_FIELDS:
        eprint(
            Template("  [edit_plan] Campo no editable: $field").substitute(field=field)
        )
        eprint(
            Template("  Editables: $fields").substitute(
                fields=", ".join(sorted(_EDITABLE_FIELDS))
            )
        )
        return None
    # Verify task exists in plan.json
    plan_path = os.path.join(chain_dir, "plan.json")
    if os.path.exists(plan_path):
        pd = load_json(plan_path)
        if pd:
            pl = pd.get("plan", pd)
            found = any(t.get("task_id") == tid for t in pl.get("tasks", []))
            if not found:
                eprint(
                    Template(
                        "  [edit_plan] Tarea no encontrada en plan.json: $tid"
                    ).substitute(tid=tid)
                )
                return None
    return [
        sys.executable,
        _SPRING_SCRIPT,
        "update-task",
        chain_dir,
        tid,
        f"--{field}",
        value,
    ]


def _build_remove_task_cmd(
    chain_dir: str, task_id: str | None, flags: dict[str, str]
) -> list[str] | None:
    """Construye el comando remove-task."""
    tid = flags.get("task_id", task_id or "")
    if not tid:
        eprint("  [edit_plan] --task_id es requerido para remove-task")
        return None
    return [
        sys.executable,
        _SPRING_SCRIPT,
        "remove",
        chain_dir,
        tid,
        "--force",
    ]


def _build_update_goal_cmd(
    chain_dir: str, flags: dict[str, str]
) -> list[str] | None:
    """Construye el comando update-goal."""
    value = flags.get("value", "")
    if not value:
        eprint("  [edit_plan] --value es requerido para update-goal")
        return None
    return [
        sys.executable,
        _SPRING_SCRIPT,
        "update-goal",
        chain_dir,
        "--description",
        value,
    ]


def _execute_plan_edit(
    chain_dir: str | None, task_id: str | None, flags: dict[str, str]
) -> bool:
    """Execute plan edit action. Returns True on success, False on failure.

    Valida datos ANTES de ejecutar. Si falla, el LLM se queda en EDIT_PLAN.
    """
    if not chain_dir:
        return False
    action = flags.get("action", "")
    if not os.path.exists(_SPRING_SCRIPT):
        eprint("  [edit_plan] spring.py no encontrado")
        return False

    if action == "add-task":
        cmd = _build_add_task_cmd(chain_dir, flags)
        if cmd is None:
            return False
    elif action == "update-task":
        cmd = _build_update_task_cmd(chain_dir, task_id, flags)
        if cmd is None:
            return False
    elif action == "remove-task":
        cmd = _build_remove_task_cmd(chain_dir, task_id, flags)
        if cmd is None:
            return False
    elif action == "update-goal":
        cmd = _build_update_goal_cmd(chain_dir, flags)
        if cmd is None:
            return False
    else:
        eprint(
            Template("  [edit_plan] Accion desconocida: $action").substitute(
                action=action
            )
        )
        eprint("  Acciones: add-task, update-task, update-goal, remove-task")
        return False

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=_SCRIPT_TIMEOUT)
        if r.returncode == 0:
            print(
                Template("  [edit_plan] $action completado").substitute(action=action)
            )
            return True
        else:
            eprint(
                Template("  [edit_plan] $action fallo (exit=$code)").substitute(
                    action=action, code=str(r.returncode)
                )
            )
            if r.stdout:
                for line in r.stdout.strip().split("\n"):
                    print(Template("    | $line").substitute(line=line))
            if r.stderr:
                for line in r.stderr.strip().split("\n"):
                    eprint(Template("    ! $line").substitute(line=line))
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        eprint(Template("  [edit_plan] Error: $err").substitute(err=str(e)))
        return False


def _load_backlog(chain_dir: str) -> list[dict]:
    """Load project backlog. Returns list of pending tasks."""
    bp = _backlog_path(chain_dir)
    if not os.path.exists(bp):
        return []
    raw = load_json(bp)
    return raw if isinstance(raw, list) else []


def _save_backlog(chain_dir: str, tasks: list[dict]) -> None:
    """Save a list of pending tasks to backlog.json."""
    bp = _backlog_path(chain_dir)
    try:
        with open(bp, "w") as f:
            json.dump(tasks, f, indent=2, ensure_ascii=False)
    except (OSError, PermissionError):
        eprint("  [backlog] Error guardando backlog.json")


def _move_pending_to_backlog(chain_dir: str) -> bool:
    """Move all pending tasks from current sprint to project backlog.

    Returns True if any tasks were moved.
    """
    prog_path = os.path.join(chain_dir, "progress.json")
    plan_path = os.path.join(chain_dir, "plan.json")
    raw = load_json(prog_path)
    if not raw:
        return False
    prog = raw.get("progress", raw)
    task_states = prog.get("current", {}).get("task_states", {})
    pending = [
        tid
        for tid, st in task_states.items()
        if isinstance(st, str) and st.lower() not in _DONE_STATUSES
    ]
    if not pending:
        return False

    # Load full task data from plan.json
    backlog = _load_backlog(chain_dir)
    plan_raw = load_json(plan_path)
    if plan_raw:
        pl = plan_raw.get("plan", plan_raw)
        for t in pl.get("tasks", []):
            if t.get("task_id") in pending:
                t["_origin_sprint"] = os.path.basename(chain_dir)
                t["_moved_to_backlog_at"] = datetime.now(timezone.utc).isoformat()
                backlog.append(t)

    _save_backlog(chain_dir, backlog)
    print(
        Template("  [backlog] $n tareas movidas a backlog del proyecto").substitute(
            n=len(pending)
        )
    )
    for tid in sorted(pending):
        print(Template("    $tid").substitute(tid=tid))
    return True


def _mark_task_cancelled(chain_dir: str, task_id: str, reason: str = "") -> None:
    """Mark a task as cancelled in progress.json task_states.

    Called when the user chooses CANCEL from USER_GATE or REPORT_GATE.
    Updates task_states[task_id] = 'cancelled' and logs the decision.
    """
    prog_path = os.path.join(chain_dir, "progress.json")
    if not os.path.exists(prog_path):
        return
    raw = load_json(prog_path)
    if not raw:
        return
    prog = raw.get("progress", raw)
    ts = prog.setdefault("current", {}).setdefault("task_states", {})
    ts[task_id] = "cancelled"
    raw.pop("_checksum", None)
    try:
        with open(prog_path, "w") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)
        print(
            Template("  [cancel] Tarea $tid marcada como cancelled").substitute(
                tid=task_id
            )
        )
        if reason:
            print(Template("    Motivo: $reason").substitute(reason=reason))
    except (OSError, PermissionError) as e:
        eprint(
            Template("  [cancel] Error guardando progress.json: $err").substitute(
                err=str(e)
            )
        )


def _load_decision_log(chain_dir: str) -> list[dict]:
    """Load decision log from progress.json."""
    prog_path = os.path.join(chain_dir, "progress.json")
    if not os.path.exists(prog_path):
        return []
    raw = load_json(prog_path)
    if not raw:
        return []
    prog = raw.get("progress", raw)
    return prog.get("current", {}).get("walker_data", {}).get("decision_log", [])


def _persist_rejected_tasks(chain_dir: str, rejected: list) -> None:
    """Store rejected_tasks in progress.json (walker-managed field)."""
    prog_path = os.path.join(chain_dir, "progress.json")
    if not os.path.exists(prog_path):
        return
    raw = load_json(prog_path)
    if not raw:
        return
    prog = raw.get("progress", raw)
    prog.setdefault("current", {}).setdefault("walker_data", {})["rejected_tasks"] = (
        rejected
    )
    raw.pop("_checksum", None)
    try:
        with open(prog_path, "w") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)
    except (OSError, PermissionError) as e:
        eprint(f"  [error] Error escribiendo plan edit en {prog_path}: {e}")


def _load_rejected_tasks(chain_dir: str) -> set[str]:
    """Load rejected tasks from progress.json (persistent storage)."""
    prog_path = os.path.join(chain_dir, "progress.json")
    if not os.path.exists(prog_path):
        return set()
    raw = load_json(prog_path)
    if not raw:
        return set()
    prog = raw.get("progress", raw)

    rejected = set()

    walker_data = prog.get("current", {}).get("walker_data", {})
    rejected.update(walker_data.get("rejected_tasks", []))

    task_states = prog.get("current", {}).get("task_states", {})
    for task_id, status in task_states.items():
        if isinstance(status, str) and status.lower() == "rejected":
            rejected.add(task_id)

    return rejected
