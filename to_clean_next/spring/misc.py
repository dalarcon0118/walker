"""spring/misc.py — Misc commands"""

from __future__ import annotations

import os
from .common import _validate_schema, eprint, load_json, write_json


def cmd_diff(args: list[str]) -> int:
    """Muestra diferencias entre plan.json y sprint-state.json.

    Uso: spring.py diff <chain_dir>
    """
    if len(args) < 1:
        eprint("❌ Uso: spring.py diff <chain_dir>")
        return 2

    chain_dir = os.path.abspath(args[0])
    plan_path = os.path.join(chain_dir, "plan.json")
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")

    if not os.path.exists(plan_path) or not os.path.exists(sprint_state_path):
        eprint("❌ plan.json o sprint-state.json no encontrados")
        return 2

    plan = load_json(plan_path)
    progress = load_json(sprint_state_path)
    if not plan or not progress:
        return 2

    pl = plan.get("plan", plan)
    prog = progress

    # SSOT: progress.current.task_states
    real_statuses = (
        progress.get("progress", {}).get("current", {}).get("task_states", {})
    )
    ts = real_statuses or prog.get("tasks", {})

    print(f"Diff: plan.json vs sprint-state.json — {len(pl.get('tasks', []))} tasks")

    for t in pl.get("tasks", []):
        tid = t["task_id"]
        prog_status = ts.get(tid, "not in progress")
        task_ok = t
        # Check if fields match
        if task_ok.get("objective", "")[:20] != "":  # basic sanity
            pass
        icon = "✅" if prog_status != "not in progress" else "❌"
        {"completed": "✅", "pending": "⬜", "not in progress": "❌"}.get(
            prog_status, "❓"
        )
        print(f"  {icon} {tid:<10} plan:exists | progress:{prog_status}")
    return 0


def cmd_blockers(args: list[str]) -> int:
    """Lista tareas bloqueadas por alto riesgo.

    Uso: spring.py blockers <chain_dir>
    """
    if len(args) < 1:
        eprint("❌ Uso: spring.py blockers <chain_dir>")
        return 2

    chain_dir = os.path.abspath(args[0])
    plan_path = os.path.join(chain_dir, "plan.json")

    if not os.path.exists(plan_path):
        eprint("❌ plan.json no encontrado")
        return 2

    plan = load_json(plan_path)
    if not plan:
        return 2

    pl = plan.get("plan", plan)
    blocked = []

    for t in pl.get("tasks", []):
        risks = t.get("risks", {})
        curvature = risks.get("curvature", 0)
        pre_churn = risks.get("pre_churn", 0)
        trust = risks.get("commit_trust", 1)

        reasons = []
        if curvature >= 0.4:
            reasons.append(f"alta curvatura ({curvature})")
        if pre_churn >= 0.3:
            reasons.append(f"alto pre_churn ({pre_churn})")
        if trust < 0.7:
            reasons.append(f"baja confianza ({trust})")

        if reasons:
            blocked.append((t["task_id"], curvature, ", ".join(reasons)))

    if not blocked:
        print("(sin tareas bloqueadas)")
        return 0

    print(f"Tareas bloqueadas ({len(blocked)}):")
    for tid, curv, reasons in sorted(blocked, key=lambda x: -x[1]):
        print(f"  ⛔ {tid}: {reasons}")
    return 0


def cmd_rebase(args: list[str]) -> int:
    """Rebase sprint-state.json to align with plan.json.

    Detects tasks in progress that no longer exist in plan (orphaned),
    marks them as ``cancelled``, and adds new tasks from plan as
    ``pending``. Preserves states of existing tasks.

    Usage:
      spring.py rebase <chain_dir> [--dry-run]

    Exit codes:
      0 → Rebase completed successfully
      2 → Error (missing files, invalid JSON)
    """
    positional, flags = _parse_args(args)
    if len(positional) != 1:
        eprint("❌ Uso: spring.py rebase <chain_dir> [--dry-run]")
        eprint("  Rebase alinea sprint-state.json con plan.json:")
        eprint("  - Tasks huérfanas (en progress pero no en plan) → cancelled")
        eprint("  - Tasks nuevas (en plan pero no en progress) → pending")
        eprint("  - Tasks existentes preservan su estado")
        return 2

    chain_dir = os.path.abspath(positional[0])
    dry_run = "dry-run" in flags

    plan_path = os.path.join(chain_dir, "plan.json")
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")

    plan = load_json(plan_path)
    progress = load_json(sprint_state_path)
    if not plan:
        eprint(f"❌ plan.json no encontrado en {chain_dir}")
        return 2
    if not progress:
        eprint(f"❌ sprint-state.json no encontrado en {chain_dir}")
        return 2

    pl = plan.get("plan", plan)
    prog = progress
    chain_id = pl.get("meta", {}).get("chain_id", os.path.basename(chain_dir))

    plan_tasks: list[dict] = pl.get("tasks", [])
    task_states: dict[str, str] = prog.setdefault("current", {}).setdefault(
        "task_states", {}
    )
    trail: list[dict] = prog.setdefault("trail", [])

    # Build sets
    plan_task_ids = {t.get("task_id", "") for t in plan_tasks}
    progress_task_ids = set(task_states.keys())

    orphaned = progress_task_ids - plan_task_ids
    new_tasks = plan_task_ids - progress_task_ids

    print("=" * 70)
    print(f"🔄 REBASE: Alineando progress con plan — {chain_id}")
    print("=" * 70)
    if dry_run:
        print("  🔧 --dry-run mode: sin cambios en disco")
    print()

    # ── Phase 1: Mark orphaned tasks as cancelled ───────────────
    if orphaned:
        print(f"  🗑️  Tasks huérfanas ({len(orphaned)}):")
        for tid in sorted(orphaned):
            old_state = task_states.get(tid, "unknown")
            task_states[tid] = "cancelled"
            print(f"     · {tid} ({old_state} → cancelled)")
        print()
    else:
        print("  ✅ No hay tasks huérfanas en progress.")
        print()

    # ── Phase 2: Add new tasks as pending ───────────────────────
    if new_tasks:
        print(f"  🆕 Tasks nuevas ({len(new_tasks)}):")
        for tid in sorted(new_tasks):
            task_states[tid] = "pending"
            task_obj = next((t for t in plan_tasks if t.get("task_id") == tid), {})
            desc = task_obj.get("objective", "")[:60]
            print(f"     · {tid} → pending — {desc}")
        print()
    else:
        print("  ✅ No hay tasks nuevas en el plan.")
        print()

    # ── Phase 3: Preserve existing task states ──────────────────
    preserved = progress_task_ids & plan_task_ids
    if preserved:
        state_counts: dict[str, int] = {}
        for tid in sorted(preserved):
            s = task_states.get(tid, "unknown")
            state_counts[s] = state_counts.get(s, 0) + 1
        count_summary = ", ".join(f"{s}: {c}" for s, c in sorted(state_counts.items()))
        print(f"  ✅ Tasks preservadas: {len(preserved)} ({count_summary})")
        print()

    # ── Phase 4: Summary ────────────────────────────────────────
    new_total = len(plan_task_ids)
    active = sum(1 for s in task_states.values() if s in ("pending", "in_progress"))
    completed = sum(1 for s in task_states.values() if s == "completed")
    cancelled = sum(1 for s in task_states.values() if s == "cancelled")
    print(
        f"  📊 Resumen: {new_total} tasks total | {completed} completadas | {active} activas | {cancelled} canceladas"
    )
    print()

    # ── Trail entry ─────────────────────────────────────────────

    trail.append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "distance": 0.0,
            "event": f"REBASE: {len(orphaned)} orphaned → cancelled, {len(new_tasks)} new → pending",
        }
    )

    # ── Write + validate (single atomic write) ──────────────────
    if not dry_run:
        write_json(sprint_state_path, progress)

        valid = _validate_schema(sprint_state_path, "sprint-state.schema.json")
        if not valid:
            eprint()
            eprint("=" * 70)
            eprint("❌ ENFORCEMENT BLOCKED: Schema validation failed post-rebase")
            eprint("=" * 70)
            eprint("  El rebase produjo sprint-state.json inválido.")
            eprint("  Revisa los errores de schema arriba y corrige el plan.")
            eprint("=" * 70)
            return 2

        print("  ✅ sprint-state.json válido contra schema")
    else:
        print("  🔧 --dry-run: cambios no persistidos")

    print("─" * 70)
    print(f"✅ REBASE COMPLETADO: {chain_id}")
    print("─" * 70)
    print()
    print("📋 Siguiente paso:")
    print(f"   python3 scripts/spring.py status {chain_dir}")
    print()
    print("💡 El sprint-state.json ahora refleja el plan actual.")
    print("   Tasks huérfanas canceladas, nuevas tasks agregadas como pending.")
    print("=" * 70)

    return 0
