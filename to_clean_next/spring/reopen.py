"""spring/reopen.py — Reopen sprint"""

from __future__ import annotations

import os
from .common import (
    _now_iso,
    _validate_schema,
    eprint,
    find_project_root,
    load_json,
    write_json,
)


def cmd_reopen(args: list[str]) -> int:
    """Reopen a closed sprint.

    Usage:
      spring.py reopen <chain_dir>
    """
    positional = [a for a in args if not a.startswith("--")]

    if len(positional) != 1:
        eprint("❌ Uso: spring.py reopen <chain_dir>")
        return 2

    chain_dir = os.path.abspath(positional[0])
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")
    plan_path = os.path.join(chain_dir, "plan.json")
    chain_id = os.path.basename(os.path.normpath(chain_dir))
    find_project_root()

    plan = load_json(plan_path)
    progress = load_json(sprint_state_path)
    if plan is None or progress is None:
        eprint(f"❌ ERROR: plan.json o sprint-state.json no encontrado en {chain_dir}")
        return 2

    pl = plan.get("plan", plan)
    prog = progress
    meta = prog.setdefault("meta", {})
    current = prog
    trail = prog.get("audit", {}).get("trail", [])

    if meta.get("status") != "closed":
        eprint(
            f"❌ ERROR: El sprint '{chain_id}' no está cerrado (status={meta.get('status')})."
        )
        eprint("   Solo se pueden reabrir sprints cerrados.")
        return 2

    print("=" * 70)
    print(f"🔓 REABRIENDO SPRING: {meta.get('chain_id', chain_id)}")
    print("=" * 70)

    meta["status"] = "active"
    meta.pop("closed_at", None)
    meta["updated_at"] = _now_iso()
    sprint_state["state"] = "active"
    first_task_id = (
        pl.get("tasks", [{}])[0].get("task_id", "") if pl.get("tasks") else ""
    )
    first_task_type = (
        pl.get("tasks", [{}])[0].get("type", "") if pl.get("tasks") else ""
    )
    first_task_desc = (
        pl.get("tasks", [{}])[0].get("task_description", "") if pl.get("tasks") else ""
    )
    current["next_action"] = {
        "task_id": first_task_id,
        "candidate": first_task_desc,
        "type": first_task_type,
        "work_type": first_task_type,
        "target": "",
        "delta": 0.5,
        "confidence": 0.80,
        "risk": 0.20,
        "score": 0.50,
        "rationale": "Sprint reabierto para continuar",
    }
    current.pop("delta_metrics", None)

    new_trail = []
    for entry in trail:
        event = entry.get("event", "")
        if event.startswith("🔒 SPRINT CLOSED"):
            continue
        new_trail.append(entry)
    trail.clear()
    trail.extend(new_trail)

    write_json(sprint_state_path, progress)
    prog_valid = _validate_schema(sprint_state_path, "sprint-state.schema.json")
    if not prog_valid:
        eprint()
        eprint("=" * 70)
        eprint("❌ ENFORCEMENT BLOCKED: sprint-state.json inválido después de reabrir")
        eprint("=" * 70)
        if os.path.exists(sprint_state_path):
            os.remove(sprint_state_path)
        return 2

    print("  ✅ sprint-state.json válido contra schema")
    print(f"  ✅ Sprint reabierto: {chain_id}")
    print("  ✅ Estado: active")
    print(f"  📝 Entradas de trail preservadas: {len(trail)}")
    print()
    print("─" * 70)
    print("✅ SPRING REABIERTO EXITOSAMENTE")
    print("─" * 70)
    print()
    print("📋 Siguiente paso:")
    print(f"   python3 scripts/task.py advance {chain_dir} TASK-INT-001")

    return 0
