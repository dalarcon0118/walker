"""spring/close.py — Close sprint"""

from __future__ import annotations

import os
import shutil
from .common import (
    _get_project_id,
    _load_sprint_progress,
    _now_iso,
    _validate_schema,
    eprint,
    find_project_root,
    load_json,
    write_json,
)
from .create import _detect_obsolete_risks, _run_test_predicate_verification


def _show_evidence_blocks(plan: dict, project_root: str) -> dict:
    """Display per-predicate evidence dashboard via prompt_engine.py."""
    try:
        from _internal.prompt_engine import build_close_evidence

        print(build_close_evidence(plan, project_root))
    except ImportError:
        print("  ⚠️  prompt_engine.py no disponible")
    return {"total": 0}  # counts computed inside build_close_evidence


def cmd_close(args: list[str]) -> int:
    """Close a sprint with composability verification.

    Usage:
      spring.py close <chain_dir> [--force] [--dry-run] [--confirm]
    """
    positional = [a for a in args if not a.startswith("--")]
    flags = [a for a in args if a.startswith("--")]

    if len(positional) != 1:
        eprint("❌ Uso: spring.py close <chain_dir> [--force] [--dry-run] [--confirm]")
        return 2

    chain_dir = os.path.abspath(positional[0])
    dry_run = "--dry-run" in flags
    force = "--force" in flags
    confirm = "--confirm" in flags

    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")
    plan_path = os.path.join(chain_dir, "plan.json")
    chain_id = os.path.basename(os.path.normpath(chain_dir))
    project_root = find_project_root()

    # Read data
    plan = load_json(plan_path)
    progress = _load_sprint_progress(chain_dir)
    if plan is None or progress is None:
        eprint(f"❌ ERROR: plan.json o sprint-state.json no encontrado en {chain_dir}")
        return 2

    project_id = _get_project_id(plan)
    pl = plan.get("plan", plan)
    prog = progress
    meta = prog.setdefault("meta", {})
    current = prog.get("current", {})
    task_states = current.setdefault("task_states", {})
    trail = prog.get("trail", [])
    risks = current.setdefault("risks", [])
    tasks = pl.get("tasks", [])

    print("=" * 70)
    print(f"🔒 CERRANDO SPRING: {meta.get('chain_id', chain_id)}")
    print("=" * 70)
    print(f"📍 Project root: {project_root}")
    print(f"📂 Chain:        {chain_dir}")
    if dry_run:
        print("🔧 Mode:         --dry-run (sin cambios)")
    print()

    # ── Evidence blocks (early, before pending-tasks gate) ────
    _show_evidence_blocks(plan, project_root)

    # 1. Validate all tasks completed
    total_tasks = max(len(tasks), len(task_states))
    completed_tasks = sum(1 for t in task_states.values() if t == "completed")
    pending_tasks = [
        tid
        for tid, state in task_states.items()
        if state in ("pending", "in_progress", "ready_for_review")
    ]
    print(f"📊 Tareas: {completed_tasks}/{total_tasks} completadas")
    if pending_tasks:
        print(f"  ❌ Tareas pendientes: {', '.join(pending_tasks)}")
        if not force:
            eprint()
            eprint(
                "❌ ERROR: Hay tareas pendientes. Usa --force para cerrar igualmente."
            )
            eprint(f"   Pendientes: {', '.join(pending_tasks)}")
            return 1
        print("  ⚠️  --force activo: cerrando con tareas pendientes")

    # Validate task quality: each task must have description and DoD
    bad_tasks = []
    for t in tasks:
        tid = t.get("task_id", "?")
        obj = t.get("objective", "")
        rs = t.get("result_state", "")
        if task_states.get(tid) == "completed":
            if len(obj) < 10:
                bad_tasks.append(f"{tid}: objective demasiado corto ({len(obj)} chars)")
            if len(rs) < 10:
                bad_tasks.append(
                    f"{tid}: result_state (DoD) demasiado corto ({len(rs)} chars)"
                )
    if bad_tasks:
        print("  ❌ Tareas con descripcion o DoD insuficiente:")
        for bt in bad_tasks:
            print(f"     - {bt}")
        if not force:
            eprint()
            eprint("❌ ERROR: Tareas completadas con descripcion o DoD insuficiente.")
            eprint(
                "   Cada tarea debe tener objective >= 10 chars y result_state >= 10 chars."
            )
            eprint(
                "   Usa spring.py update-task <chain> <task_id> --objective 'nueva desc'"
            )  # noqa: E501
            eprint(
                "   Usa spring.py update-task <chain> <task_id> --result_state 'nuevo DoD'"
            )  # noqa: E501
            eprint("   O usa --force para cerrar igualmente.")
            return 1
        print("  ⚠️  --force activo: cerrando con tareas de baja calidad")

    # 2. Detect obsolete risks
    print()
    total_risks = len(risks)
    risks_updated = _detect_obsolete_risks(risks, project_root)
    obsolete_count = sum(1 for r in risks_updated if r.get("status") == "obsolete")
    valid_count = sum(1 for r in risks_updated if r.get("status") == "valid")
    unchecked_count = sum(1 for r in risks_updated if r.get("status") == "unchecked")

    if total_risks > 0:
        print(f"\n📋 Riesgos: {total_risks} total")
        print(f"  ✅ Válidos:     {valid_count}")
        if obsolete_count > 0:
            print(f"  ⚠️  Obsoletos:   {obsolete_count}")
        if unchecked_count > 0:
            print(f"  ➖ Sin verificar: {unchecked_count}")
    else:
        print("📋 Riesgos: ninguno registrado")

    # 3. Compute closure summary (metadata-based)
    total_predicates = sum(
        [
            len(pl.get("goal", {}).get("predicates", {}).get("must_exist", [])),
            len(pl.get("goal", {}).get("predicates", {}).get("must_not_exist", [])),
            len(pl.get("goal", {}).get("predicates", {}).get("invariants", [])),
        ]
    )
    completed_predicates = set()
    for t in tasks:
        if task_states.get(t["task_id"]) == "completed":
            for p in t.get("validation", {}).get("predicates", []):
                completed_predicates.add(p)
    metadata_satisfied = len(completed_predicates)
    goal_desc = pl.get("goal", {}).get("description", "N/A")[:120]

    # 4. REAL-CODE VERIFICATION (FASE 2 — TEST EJECUTABLE)
    # Verifica los predicates ejecutando test_predicate_validation.py
    # contra el servidor real, en lugar de confiar en metadata escrita
    # por el LLM.
    print("\n🔍 REAL-CODE VERIFICATION (FASE 2):")
    print("  ──")
    real_verification = _run_test_predicate_verification(plan, project_root)
    real_satisfied = real_verification["real_satisfied"]
    real_unsatisfied = real_verification["unsatisfied"]
    print(
        f"  Metadata:  {metadata_satisfied}/{total_predicates} predicates marcados como completados"
    )
    print(
        f"  Real test: {real_satisfied}/{total_predicates} predicates verificados por test ejecutable"
    )
    if real_verification.get("_error"):
        print(f"  ⚠️  {real_verification['_error']}")
    print()

    distance = max(0.0, 1.0 - (real_satisfied / max(total_predicates, 1)))
    actual_satisfied = real_satisfied

    # ════════════════════════════════════════════════════════════════
    #  ENFORCEMENT: Composability check — ALL predicates must be
    #  REALMENTE verificados contra código para cerrar. Partial closure
    #  is an antipattern. This is NON-NEGOTIABLE (no --force bypass).
    # ════════════════════════════════════════════════════════════════
    if total_predicates > 0 and actual_satisfied < total_predicates:
        eprint()
        eprint("=" * 70)
        eprint(
            f"❌ COMPOSITION BLOCKED: {actual_satisfied}/{total_predicates} predicates verificados por test ejecutable"
        )
        eprint("=" * 70)
        eprint("  Un sprint SOLO se cierra cuando TODOS los predicates pasan")
        eprint("  el test ejecutable test_predicate_validation.py.")
        eprint(
            f"  Faltan {total_predicates - actual_satisfied} predicates por verificar."
        )
        eprint(f"  Faltan {total_predicates - actual_satisfied} predicates:")
        eprint()

        if _HAVE_ADVANCE and real_unsatisfied:
            for up in real_unsatisfied:
                icon = "➕" if up["type"] in ("must_exist", "invariants") else "➖"
                eprint(f"  {icon} {up['predicate']}")
                eprint(f"     └─ {up['reason']}")
        else:
            # Fallback: show metadata unsatisfied
            unsatisfied_preds = []
            for ps in pl.get("goal", {}).get("predicates", {}).get("must_exist", []):
                if ps not in completed_predicates:
                    unsatisfied_preds.append(ps)
            for ps in (
                pl.get("goal", {}).get("predicates", {}).get("must_not_exist", [])
            ):
                if ps not in completed_predicates:
                    unsatisfied_preds.append(ps)
            for ps in pl.get("goal", {}).get("predicates", {}).get("invariants", []):
                if ps not in completed_predicates:
                    unsatisfied_preds.append(ps)
            for up in unsatisfied_preds:
                eprint(f"    • {up} (metadata dice incompleto)")

        eprint()
        eprint("  Acciones requeridas:")
        eprint("  1. Identifica qué tareas cubren los predicates faltantes")
        eprint("  2. Implementa el código que falta o corrige el existente")
        eprint(
            "  3. Avanza esas tareas: python3 scripts/task.py advance <chain> <task_id>"
        )
        eprint("  4. Una vez completadas todas, reintenta el cierre")
        if _HAVE_ADVANCE:
            eprint()
            eprint(
                "  💡 Los predicates listados arriba NO se encontraron en el código real."
            )
            eprint(
                "     Si el predicate es 'must_exist', necesitas implementar la función/clase."
            )
            eprint(
                "     Si el predicate es 'must_not_exist', necesitas eliminar el código."
            )
        eprint()
        eprint(
            "  NOTA: --force NO permite saltar predicates. El cierre es composicional."
        )
        eprint("  Si el goal cambió, crea un NUEVO sprint con el nuevo G_objetivo.")
        eprint("=" * 70)
        return 2

    # USER CONFIRMATION GATE: require explicit confirmation before closing
    if not dry_run and not confirm:
        print()
        print("=" * 70)
        print(f"⚠️  CONFIRMACIÓN REQUERIDA: ¿Cerrar el sprint '{chain_id}'?")
        print("=" * 70)
        print(f"  📊 Tareas:        {completed_tasks}/{total_tasks} completadas")
        print(f"  📋 Predicates:    {actual_satisfied}/{total_predicates} verificados")
        print(f"  📈 Distance:      {round(distance, 4)}")
        print(f"  ⚠️  Riesgos:       {valid_count} válidos, {obsolete_count} obsoletos")
        print()
        print("  Para cerrar, ejecuta:")
        print(f"     python3 scripts/spring.py close {chain_dir} --confirm")
        print()
        print("  O usa --dry-run para revisar sin cerrar:")
        print(f"     python3 scripts/spring.py close {chain_dir} --dry-run")
        print("=" * 70)
        return 3

    # 5. Build closure trail entry
    closure_entry = {
        "at": _now_iso(),
        "distance": round(distance, 4),
        "event": (
            f"🔒 SPRINT CLOSED. {completed_tasks}/{total_tasks} tareas. "
            f"{actual_satisfied}/{total_predicates} predicates."
        ),
        "closure": {
            "tasks_completed": completed_tasks,
            "tasks_total": total_tasks,
            "predicates_satisfied": actual_satisfied,
            "predicates_total": total_predicates,
            "distance": round(distance, 4),
            "risks_valid": valid_count,
            "risks_obsolete": obsolete_count,
            "risks_unchecked": unchecked_count,
        },
    }

    # 5. Apply changes
    if not dry_run:
        meta["status"] = "closed"
        meta["closed_at"] = _now_iso()
        trail.append(closure_entry)
        current["next_action"] = None
        current["finished"] = True
        delta_metrics = current.setdefault("delta_metrics", {})
        delta_metrics["closure"] = {
            "closed_at": _now_iso(),
            "satisfied_predicates": actual_satisfied,
            "total_predicates": total_predicates,
            "distance_at_close": round(distance, 4),
            "risks_obsolete": obsolete_count,
            "risks_total": total_risks,
        }
        write_json(sprint_state_path, progress)

        # ── ENFORCEMENT: Validate sprint-state.json after write ────
        # If validation fails, close is blocked. Dirty data must not persist.
        prog_valid = _validate_schema(sprint_state_path, "sprint-state.schema.json")
        if not prog_valid:
            eprint()
            eprint("=" * 70)
            eprint(
                "❌ ENFORCEMENT BLOCKED: sprint-state.json inválido después del cierre"
            )
            eprint("=" * 70)
            eprint("  El sprint NO se cerró. Los datos generados son inválidos.")
            eprint("  Revisa los errores arriba, corrige y reintenta.")
            eprint("=" * 70)
            # Remove the invalid sprint-state.json to avoid dirty data
            if os.path.exists(sprint_state_path):
                os.remove(sprint_state_path)
            return 2

        print("  ✅ sprint-state.json válido contra schema")

        _kv_save(chain_id, "progress", progress, project_id)

        # Update current.json
        current_json_path = os.path.join(os.path.dirname(chain_dir), "current.json")
        if os.path.exists(os.path.dirname(current_json_path)):
            shutil.copy(sprint_state_path, current_json_path)
            print("  📋 current.json actualizado con estado de cierre")

        print()
        print("─" * 70)
        print("✅ SPRING CERRADO EXITOSAMENTE")
    else:
        print()
        print("─" * 70)
        print("🔍 DRY-RUN — no se escribieron cambios")

    # 6. Dashboard
    print()
    print("📊 SPRING CLOSURE DASHBOARD")
    print("─" * 70)
    print(f"  Chain ID:     {meta.get('chain_id', chain_id)}")
    print(f"  Goal:         {goal_desc}")
    print("  Status:       🔒 CLOSED")
    print(f"  Tareas:       {completed_tasks}/{total_tasks}")
    if pending_tasks:
        print(f"  Pendientes:   {', '.join(pending_tasks)}")
    print(
        f"  Predicates:   {actual_satisfied}/{total_predicates} ({round(distance * 100)}% distance)"
    )
    print(f"  Riesgos:      {valid_count} válidos, {obsolete_count} obsoletos")
    print()
    print("📋 RIESGOS DETECTADOS:")
    if total_risks == 0:
        print("  (ninguno)")
    else:
        for r in risks_updated:
            status_icon = {
                "valid": "✅",
                "obsolete": "⚠️  OBSOLETO",
                "unchecked": "➖",
            }.get(r.get("status", ""), "❓")
            severity_icon = {
                "high": "🔴",
                "medium": "🟡",
                "low": "🟢",
            }.get(r.get("severity", ""), "⚪")
            print(
                f"  {severity_icon} [{status_icon:>12}] {r.get('description', '')[:80]}"
            )

    print()
    print(f"📈 Trail entries: {len(trail)}")
    print(f"📈 Distance final: {round(distance, 4)}")
    if len(trail) >= 2:
        first_d = trail[0].get("distance", 1.0)
        last_d = round(distance, 4)
        delta_pct = round((first_d - last_d) * 100, 1)
        print(f"📈 Δ distancia: -{delta_pct}%")
    print("─" * 70)

    # 7. Decision protocol
    backlog_path_hint = (
        f".cognitive/projects/{project_id}/delta/backlog.jsonl"
        if project_id
        else ".cognitive/delta/backlog.jsonl"
    )
    print()
    print("📋 DECISION PROTOCOL — siguiente paso:")
    print()
    if pending_tasks and not dry_run:
        print("   ⚠️  Sprint cerrado con tareas pendientes (--force).")
        print(f"      Pendientes: {', '.join(pending_tasks)}")
    elif obsolete_count > 0:
        print("   ⚠️  Riesgos obsoletos detectados (archivos ya no existen).")
    else:
        print("   ✅ Composición válida — todos los predicates satisfechos.")
    print()
    print("   ▶️  Nuevo ciclo: define un nuevo G_objetivo y crea otro sprint.")
    print(f"   ▶️  Revisar backlog: {backlog_path_hint}")
    print()
    print("💡 Sprint cerrado. El LLM solo necesita decidir el próximo objetivo.")
    print("=" * 70)

    return 0
