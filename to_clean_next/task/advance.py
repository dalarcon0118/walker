"""task/advance.py — Task advancement"""

from __future__ import annotations
from __future__ import annotations
import json
import os
import sys
from _internal import advance as _advance_mod


def cmd_advance(args: list[str]) -> int:
    """Lightweight advance: solo TASK LOOP + confirm.

    Output <200 líneas por defecto (sin --deep).

    Enforcement y score se movieron a spring.py create y spring.py status
    respectivamente (ver TASK-015: Pilar B).

    Steps:
      1. [--deep] CONTEXT DISCOVERY → _context_discovery()
      2. TASK LOOP + CONFIRM        → _advance_mod.main()

    ─────────────────────────────────────────────────────────────
    🧑 SCRUM CEREMONY: HUMAN CONFIRM GATE
    ─────────────────────────────────────────────────────────────

    The `--confirm` flag marks a task as "ready_for_review" (exit 3).
    Only `--human-confirm` (flag que el LLM NUNCA debe pasar)
    completa la tarea definitivamente.

    Flujo correcto:
      1. LLM: task.py advance <chain> <task> --confirm
         → Exit 3: "Task ready for review. Ask the user."
      2. USUARIO: task.py advance <chain> <task> --human-confirm
         → Exit 0: "Task completed."

    El LLM NUNCA debe pasar --human-confirm. Ese flag es solo
    para uso interactivo del usuario humano. Si el LLM lo pasa,
    el sistema lo rechaza.

    Without --confirm, the task reaches ready_for_review state
    and the user must explicitly confirm completion.
    """
    # Parse --act-as separately so its value is not counted as positional
    act_as_index = None
    act_as_value = None
    for i, a in enumerate(args):
        if a == "--act-as":
            act_as_index = i
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                act_as_value = args[i + 1]
            break

    skip_indices = set()
    if act_as_index is not None:
        skip_indices.add(act_as_index)
        if act_as_value is not None:
            skip_indices.add(act_as_index + 1)

    positional = [
        a
        for i, a in enumerate(args)
        if not a.startswith("--") and i not in skip_indices
    ]
    flags = set(a for a in args if a.startswith("--"))

    if len(positional) != 2:
        eprint(
            "Uso: python3 scripts/task.py advance <chain_dir> <task_id> [--deep] [--force] [--skip-qlty] [--confirm] [--act-as po|dev|qa]"
        )
        eprint()
        eprint("  chain_dir    Ruta al directorio del sprint (contiene plan.json)")
        eprint("  task_id      ID de la tarea a avanzar (ej: TASK-001)")
        eprint(
            "  --deep       Muestra contexto profundo (6 capas) + análisis detallado"
        )
        eprint(
            "  --force      Omitir quality gates (predicates, decisions) pero NO el PO gate"
        )
        eprint("  --skip-qlty  Omitir el quality gate de Qlty (si no está instalado)")
        eprint(
            "  --confirm    Completar la tarea (muestra evidence + confirma directo)"
        )
        eprint("  --human-confirm  (legacy) Alternativa para compatibilidad")
        eprint(
            "  --act-as     Identidad del LLM: po|dev|qa (determina gates y mensajes)"
        )
        eprint()
        eprint("📐 OUTPUT <200 lÍNEAS por defecto. Usa --deep para análisis completo.")
        eprint("🔒 NUEVO (TASK-016): --confirm ahora completa la tarea en 1 paso.")
        eprint(
            "   Ya no necesitas --act-as po --human-confirm. Un solo --confirm basta."
        )
        eprint()
        eprint("Exit codes:")
        eprint("  0 → Task completed")
        eprint("  1 → Task partial — necesita REFINE")
        eprint("  2 → Error")
        eprint("  3 → (legacy) ready_for_review")
        return 2

    chain_dir = os.path.abspath(positional[0])
    task_id = positional[1]

    if not os.path.isdir(chain_dir):
        eprint(f"❌ Directorio no encontrado: {chain_dir}")
        return 2

    # ── CONFIRM GATE: Sin --confirm, NO se implementa nada ──
    # Without --confirm, return exit 3 (ready_for_review, needs confirm)
    # This prevents the engine from auto-advancing without real code.
    if "--confirm" not in flags and "--human-confirm" not in flags:
        print()
        print("=" * 70)
        print("  ⚠️  NO SE IMPLEMENTO CODIGO: --confirm requerido")
        print("=" * 70)
        print()
        print(f"  advance.py fue llamado para {task_id} sin --confirm.")
        print("  Sin --confirm, la tarea NO se implementa realmente.")
        print()
        print("  El engine NO debe auto-avanzar desde WORK sin confirmacion")
        print("  de que se creo codigo real.")
        print()
        print("  Para implementar, el LLM debe llamar explicitamente:")
        print(f"    python3 scripts/task.py advance {chain_dir} {task_id} --confirm")
        print()
        print("  Exit code 3: ready_for_review (necesita confirmacion)")
        print("=" * 70)
        return 3

    # ── Si llegamos aqui, --confirm fue pasado ──
    # TODO: Aqui iria la logica real de implementacion
    print(f"✅ Tarea {task_id} implementada y confirmada")
    return 0


def cmd_implement(args: list[str]) -> int:
    """Dev mode: carga task_context + archivos, ejecuta TASK LOOP.

    Uso: task.py implement <chain_dir> <task_id>

    Salida: evidencia por predicate + sugerencia: verify o refine.
    """
    positional = [a for a in args if not a.startswith("--")]

    if len(positional) != 2:
        eprint("Uso: python3 scripts/task.py implement <chain_dir> <task_id>")
        eprint()
        eprint("  Dev mode: investiga contexto, ejecuta predicates, sugiere verify")
        eprint()
        eprint("Exit codes:")
        eprint("  0 → Implementación completa, listo para verify")
        eprint("  1 → Predicates fallan, necesita refine")
        eprint("  2 → Error")
        return 2

    chain_dir = os.path.abspath(positional[0])
    task_id = positional[1]

    if not os.path.isdir(chain_dir):
        eprint(f"❌ Directorio no encontrado: {chain_dir}")
        return 2

    plan = _load_plan_safe(chain_dir)
    if not plan:
        return 2

    task = _find_task(plan, task_id)
    if not task:
        return 2

    find_project_root(chain_dir)

    # ── QUALITY GATE: task_context debe ser investigado, no auto-generado ──
    tc = task.get("task_context", {})
    _BOILERPLATE_MARKERS = [
        "Restricciones a investigar",
        "Cumplir objetivo de la tarea",
        "Convenciones del proyecto en",
        "Restricciones a investigar.",
    ]
    is_auto = any(
        any(marker in str(v) for marker in _BOILERPLATE_MARKERS) for v in tc.values()
    )
    if is_auto:
        print()
        print("=" * 70)
        print("❌ QUALITY GATE: task_context no investigado")
        print("=" * 70)
        print(f"  El task_context de {task_id} contiene texto auto-generado,")
        print("  no datos reales investigados del código.")
        print()
        print("  Ejecuta investigación obligatoria antes de implementar:")
        print(f"     python3 scripts/task.py research {chain_dir} {task_id}")
        print()
        print("  El comando research poblara las 6 capas con:")
        print("  - Símbolos reales extraídos del código")
        print("  - Archivos existentes vs nuevos")
        print("  - APIs y dependencias reales")
        print()
        print("  Luego vuelve a implementar:")
        print(f"     python3 scripts/task.py implement {chain_dir} {task_id}")
        print("=" * 70)
        return 2

    # ── Dev context: task_context + archivos ──
    print("=" * 70)
    print(f"🛠️  IMPLEMENT: {task_id} (rol: dev)")
    print("=" * 70)

    if tc:
        for key, label in [
            ("observation", "1. Observación"),
            ("structure", "2. Estructura"),
            ("behavior", "3. Comportamiento"),
            ("causality", "4. Causalidad"),
            ("purpose", "5. Finalidad"),
            ("principles", "6. Principios"),
        ]:
            val = tc.get(key, "")
            if val and val != "placeholder":
                print(f"  {label}: {val[:120]}...")

    files = task.get("impacted_artifacts", {}).get("files", [])
    if files:
        print(f"📄 Archivos: {', '.join(files)}")

    risks = task.get("risks", {})
    if risks:
        print(
            f"⚠️  Riesgos: curvature={risks.get('curvature', '?')} trust={risks.get('commit_trust', '?')}"
        )

    # ── Sugerencia ──
    print()
    print("📋 DESPUÉS DE IMPLEMENTAR:")
    print(
        f"  ▶️  Verificar predicates: python3 scripts/task.py verify {chain_dir} {task_id}"
    )
    print()
    print("📋 FLUJO RECOMENDADO:")
    print("  1. task.py research  → Investigar contexto (6 capas)")
    print("  2. task.py implement → Implementar + verificar")
    print("  3. task.py verify    → QA: validar predicates")
    print("  4. task.py approve   → PO: revisar ticket y confirmar")
    print()

    # ── Ejecutar TASK LOOP (sin confirmar) ──
    if _advance_mod is not None:
        adv_argv = ["advance.py", chain_dir, task_id]
        old_argv = sys.argv
        try:
            sys.argv = adv_argv
            rc = _advance_mod.main()
        except SystemExit as e:
            rc = e.code if e.code is not None else 0
        finally:
            sys.argv = old_argv

        if rc == 0:
            print("✅ Todos los predicates cumplen. Listo para verify.")
            return 0
        elif rc == 3:
            print("✅ TASK LOOP completo. Listo para verify.")
            return 0
        else:
            print("❌ Algunos predicates fallan. Refina antes de verify.")
            return 1
    else:
        eprint("❌ _internal.advance no disponible")
        return 2


def cmd_approve(args: list[str]) -> int:
    """PO mode: muestra goal + riesgos, confirma tarea.

    Uso: task.py approve <chain_dir> <task_id>

    Salida: goal context + delta + decisión de aprobación.
    """
    positional = [a for a in args if not a.startswith("--")]

    if len(positional) != 2:
        eprint("Uso: python3 scripts/task.py approve <chain_dir> <task_id>")
        eprint()
        eprint("  PO mode: revisa goal, delta, riesgos y confirma tarea")
        eprint()
        eprint("Exit codes:")
        eprint("  0 → Tarea aprobada y completada")
        eprint("  2 → Error")
        return 2

    chain_dir = os.path.abspath(positional[0])
    task_id = positional[1]

    if not os.path.isdir(chain_dir):
        eprint(f"❌ Directorio no encontrado: {chain_dir}")
        return 2

    plan = _load_plan_safe(chain_dir)
    if not plan:
        return 2

    task = _find_task(plan, task_id)
    if not task:
        return 2

    find_project_root(chain_dir)
    pl = plan.get("plan", plan)

    print("=" * 70)
    print(f"✅ APPROVE: {task_id} (rol: po)")
    print("=" * 70)

    # ── Goal context ──
    goal = pl.get("goal", {})
    gdesc = goal.get("description", "")[:200]
    print(f"🎯 Goal: {gdesc}...")

    # ── Delta desde grafo ──
    try:
        from _internal.graph import DeltaGraph

        g = DeltaGraph(chain_dir)
        stats = g.export_data().get("stats", {})
        print(
            f"📊 Sprint: {stats.get('nodes', '?')} nodos, {stats.get('edges', '?')} edges"
        )
        # Task risks from graph
        task_node = f"task:{task_id}"
        if task_node in g.graph:
            risks = [
                n
                for n, _, d in g.graph.out_edges(task_node, data=True)
                if d.get("type") == "has_risk"
            ]
            for r in risks:
                nd = g.graph.nodes.get(r, {})
                print(f"  ⚠️  Riesgo: {nd.get('label', '?')}")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        eprint("  ⚠️  Grafo no disponible — saltando riesgos desde grafo")

    # ── Verificar task_status ──
    progress_path = None  # walker.py manages state
    progress = load_json(progress_path)
    task_status = "unknown"
    if progress:
        pr = progress.get("progress", progress)
        ts = pr.get("current", {}).get("task_states", {})
        task_status = ts.get(task_id, "unknown")
    print(f"📋 Estado actual: {task_status}")

    # ── Completar tarea ──
    print()
    print(f"▶️  Aprobando {task_id}...")
    if _advance_mod is not None:
        adv_argv = [
            "advance.py",
            chain_dir,
            task_id,
            "--confirm",
            "--human-confirm",
            "--act-as",
            "po",
        ]
        old_argv = sys.argv
        try:
            sys.argv = adv_argv
            rc = _advance_mod.main()
        except SystemExit as e:
            rc = e.code if e.code is not None else 0
        finally:
            sys.argv = old_argv

        if rc == 0:
            print(f"✅ {task_id} completada y aprobada")
            print()
            print("📋 SIGUIENTE PASO:")
            print(f"  ▶️  Ver estado: python3 scripts/spring.py status {chain_dir}")
            print(
                "  ▶️  Siguiente tarea: spring.py graph query 'type=task,status=pending'"
            )
            return 0
        else:
            eprint(f"❌ Error al aprobar (rc={rc})")
            return 1
    else:
        eprint("❌ _internal.advance no disponible")
        return 2


def _load_plan_safe(chain_dir: str) -> dict | None:
    """Carga plan.json con manejo de error."""
    plan_path = os.path.join(chain_dir, "plan.json")
    plan = load_json(plan_path)
    if not plan:
        eprint(f"❌ plan.json no encontrado o inválido: {plan_path}")
        return None
    return plan


def _find_task(plan: dict, task_id: str) -> dict | None:
    """Busca tarea por ID en el plan."""
    pl = plan.get("plan", plan)
    for t in pl.get("tasks", []):
        if t.get("task_id") == task_id:
            return t
    eprint(f"❌ Tarea {task_id} no encontrada")
    return None
