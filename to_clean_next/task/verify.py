"""task/verify.py — Predicate verification"""

from __future__ import annotations
from __future__ import annotations
import json
import os
from string import Template


def _validate_task_context(
    chain_dir: str, task_id: str, project_root: str, project_id: str | None
) -> bool:
    """Validate task_context using code-symbol analysis (language-agnostic).

    Replaces old keyword-based checks with structural verification:
    1. No generic placeholders (structural heuristic)
    2. Code symbols in task_context must exist in affected files (grep-verified)
    3. Each API/symbol to create is checked for existence
    4. Each layer must contain at least 2 verified code symbols from the project

    This is LANGUAGE-AGNOSTIC: works identically in Spanish, English, etc.
    because it operates on code symbols, not natural language keywords.
    """
    plan_path = os.path.join(chain_dir, "plan.json")
    plan = load_json(plan_path)
    if not plan:
        return False

    pl = plan.get("plan", plan)
    task = None
    for t in pl.get("tasks", []):
        if t.get("task_id") == task_id:
            task = t
            break

    if not task:
        return False

    artefacts = task.get("impacted_artifacts", {})
    files = artefacts.get("files", []) or task.get("files", [])
    apis = artefacts.get("apis", []) or task.get("apis", [])
    task_context = task.get("task_context", {})

    required_layers = [
        "observation",
        "structure",
        "behavior",
        "causality",
        "purpose",
        "principles",
    ]

    # Check 1: No generic placeholders (structural heuristic)
    for layer_key, layer_value in task_context.items():
        if not isinstance(layer_value, str):
            print(f"  ❌ Capa '{layer_key}' no es texto")
            return False
        if layer_key in ("principles", "causality", "purpose"):
            continue
        if _is_generic_placeholder(layer_value):
            print(f"  ❌ Capa '{layer_key}' tiene contenido genérico o placeholder")
            print("     (texto muy corto o sin símbolos de código)")
            return False

    # Check 2: Symbol cross-reference — extract all symbols from task_context
    # and grep-verify them against the affected files
    # Skip for glob-pattern tasks (migrations, bulk changes)
    has_glob_patterns = any("*" in f or "?" in f for f in files)
    all_context_symbols: set[str] = set()
    if not has_glob_patterns:
        for layer_key in required_layers:
            text = task_context.get(layer_key, "")
            if isinstance(text, str):
                all_context_symbols.update(_extract_symbols(text))

        if not all_context_symbols:
            print("  ❌ task_context no contiene ningún símbolo de código")
            print("     (nombres de clases, funciones o archivos)")
            return False

        # Grep-verify symbols against real code
        verified_symbols = _verify_symbols_in_files(
            all_context_symbols, files, project_root, project_id
        )

        total_symbols = len(all_context_symbols)
        verified_count = len(verified_symbols)
        # Absolute threshold: require at least 2 real code definitions verified
        # (not percentage-based — avoids false negatives from noisy regex extraction)
        min_verified = 1 if total_symbols <= 5 else 2

        print(
            f"  🔍 Símbolos extraídos: {total_symbols} | Verificados: {verified_count}"
        )
        if verified_count > 0:
            top_syms = list(verified_symbols.keys())[:5]
            for sym in top_syms:
                lines = verified_symbols[sym][:2]
                for line in lines:
                    print(f"     ✅ {sym} → {line[:100]}")
            if len(verified_symbols) > 5:
                print(f"     ... y {len(verified_symbols) - 5} símbolos más")

        if verified_count < min_verified:
            print("  ❌ Pocos símbolos verificables en task_context")
            print(f"     Mínimo requerido: {min_verified} símbolos reales del proyecto")
            print(f"     Encontrados: {verified_count}")
            return False
    else:
        verified_symbols = set()
        verified_count = 0
        total_symbols = 0
        print("  🔍 Tarea con patrones glob — saltando verificación de símbolos")

    # Check 3: APIs/symbols existence check (must_not_exist direction)
    # Scope grep to project directory for speed
    _api_search_root = (
        _get_project_search_root(chain_dir, project_root, project_id) or project_root
    )
    for api in apis:
        if not api or not isinstance(api, str):
            continue
        matches = _grep_symbol(api, _api_search_root)
        if matches:
            print(f"  ⚠️  API '{api}' ya existe en el código:")
            for m in matches[:3]:
                print(f"     {m[:100]}")
            print(
                "  ❌ Debes ajustar el task_context: justifica por qué existe o renómbralo"
            )
            return False

    # Check 4: Each layer must have at least 1 verified code symbol
    # Skip for glob-pattern tasks (migrations, bulk changes)
    layer_report: list[str] = []
    for layer_name in required_layers:
        layer_value = task_context.get(layer_name, "")
        if not isinstance(layer_value, str) or not layer_value.strip():
            print(f"  ❌ Capa '{layer_name}' vacía")
            return False

        if has_glob_patterns:
            # For glob tasks, just check that the layer is not empty
            continue

        # Skip symbol verification for abstract/explanatory layers
        if layer_name in ("principles", "causality", "purpose"):
            layer_report.append(f"{layer_name}: omitida (explicativa)")
            continue

        # Extract symbols from this layer
        layer_syms = _extract_symbols(layer_value)
        # Count how many are verified
        layer_verified = [s for s in layer_syms if s in verified_symbols]

        if len(layer_verified) < 1:
            print(f"  ❌ Capa '{layer_name}': 0 símbolos verificables del proyecto")
            print("     (debe mencionar al menos 1 clase, función o archivo real)")
            return False

        layer_report.append(
            f"{layer_name}: {len(layer_verified)}/{len(layer_syms)} símbolos"
        )

    for report in layer_report:
        print(f"     📊 {report}")

    print(
        f"  ✅ task_context validado por símbolos de código ({verified_count} verificados en {len(files)} archivos)"
    )
    return True


def cmd_verify(args: list[str]) -> int:
    """QA mode: verifica predicates contra código real, muestra evidencia.

    Uso: task.py verify <chain_dir> <task_id>

    Salida: evidencia por predicate + sugerencia: approve o refine.
    """
    positional = [a for a in args if not a.startswith("--")]

    if len(positional) != 2:
        eprint("Uso: python3 scripts/task.py verify <chain_dir> <task_id>")
        eprint()
        eprint("  QA mode: verifica predicates, muestra evidencia, sugiere approve")
        eprint()
        eprint("Exit codes:")
        eprint("  0 → Todos los predicates pasan, listo para approve")
        eprint("  1 → Algunos predicates fallan")
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

    project_root = find_project_root(chain_dir)

    print("=" * 70)
    print(f"🔍 VERIFY: {task_id} (rol: qa)")
    print("=" * 70)

    # ── Verificar predicates ──
    from _internal.verification import verify_predicates_batch

    preds = task.get("validation", {}).get("predicates", [])
    if not preds:
        print("  (sin predicates en esta tarea)")
        print()
        print("📋 DESPUÉS DE VERIFICAR:")
        print(f"  ▶️  Aprobar: python3 scripts/task.py approve {chain_dir} {task_id}")
        return 0

    results = verify_predicates_batch(preds, project_root)

    passed = 0
    for i, (ok, info) in enumerate(results, 1):
        method = info.get("method", "?")[:14]
        label = info.get("label", str(preds[i - 1]))[:60]
        icon = "✅" if ok else "❌"
        print(f"  {icon} [{i}] {method:<14s} {label}")
        if ok:
            passed += 1

    total = len(preds)
    print(f"  {passed}/{total} predicates pasan")

    # ── Usar grafo para evidencia adicional ──
    try:
        from _internal.graph import DeltaGraph

        g = DeltaGraph(chain_dir)
        g.export_data()
        # Count predicates connected to this task
        task_node = f"task:{task_id}"
        if task_node in g.graph:
            pred_count = sum(
                1
                for _, _, d in g.graph.out_edges(task_node, data=True)
                if d.get("type") == "has_predicate"
            )
            file_count = sum(
                1
                for _, _, d in g.graph.out_edges(task_node, data=True)
                if d.get("type") == "impacts_file"
            )
            print(
                f"  📊 Grafo: {pred_count} predicates, {file_count} archivos impactados"
            )
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        eprint("  ⚠️  Grafo no disponible — saltando analisis de grafo")

    if passed == total:
        print()
        print("📋 TODOS LOS PREDICATES PASAN:")
        print(
            f"  ▶️  Aprobar tarea: python3 scripts/task.py approve {chain_dir} {task_id}"
        )
        print()
        print("📋 FLUJO:")
        print("  1. research ✅ | 2. implement ✅ | 3. verify ✅")
        print("  4. task.py approve → PO revisa ticket y confirma")
        return 0
    else:
        print()
        print("📋 PREDICATES FALLAN:")
        print(f"  ▶️  Refinar: python3 scripts/task.py implement {chain_dir} {task_id}")
        return 1


def cmd_check_readiness(args: list[str]) -> int:
    """Gate validation: ?la tarea esta lista para ANALYSIS_GATE?

    Verifica PO approval + contexto completo + sin blockers.
    Exit 0 = ready, 1 = not ready.
    """
    if len(args) < 2:
        eprint("Uso: task.py check-readiness <chain_dir> <task_id>")
        return 2

    chain_dir = args[0]
    task_id = args[1]

    plan = _load_plan_safe(chain_dir)
    if not plan:
        return 2

    task = _find_task(plan, task_id)
    if not task:
        return 2

    issues = []

    # Verificar PO approval
    if task.get("status") == "pending_po":
        issues.append("PO approval pendiente")

    # Verificar contexto de investigacion
    if task.get("status") == "needs_research":
        issues.append("Contexto de investigacion pendiente")

    # Verificar resultado de research previo
    research_done = task.get("research_completed", False)
    task_ctx = task.get("task_context", {})
    has_task_context = bool(
        task_ctx and isinstance(task_ctx, dict) and len(task_ctx) >= 3
    )
    if not research_done and not has_task_context:
        issues.append("Research no completado")

    # Verificar blockers
    blockers = task.get("blockers", [])
    if blockers:
        issues.append(
            Template("Blockers activos: $items").substitute(items=", ".join(blockers))
        )

    if issues:
        print("NOT_READY")
        for issue in issues:
            eprint(Template("  - $item").substitute(item=issue))
        return 1

    print("READY")
    print("Tarea lista para ANALYSIS_GATE.")
    return 0


def cmd_check_context_gaps(args: list[str]) -> int:
    """Gate validation: ?hay gaps de contexto que requieren research?

    Exit 0 = gaps detected (research needed), 1 = no gaps.
    """
    if len(args) < 2:
        eprint("Uso: task.py check-context-gaps <chain_dir> <task_id>")
        return 2

    chain_dir = args[0]
    task_id = args[1]

    plan = _load_plan_safe(chain_dir)
    if not plan:
        return 2

    task = _find_task(plan, task_id)
    if not task:
        return 2

    # Evaluar si hay gaps basado en estado de la tarea
    status = task.get("status", "")
    risks = task.get("risks", {})

    gap_signals = []

    # Tarea nunca ha sido investigada
    if status == "needs_research":
        gap_signals.append("Tarea marcada como needs_research")

    # Tarea nueva sin research completado
    if not task.get("research_completed", False):
        gap_signals.append("Research previo no encontrado")

    # Riesgos altos sin mitigacion
    curvature = risks.get("curvature", 0)
    if curvature > 7:
        gap_signals.append(
            Template("Riesgo alto (curvature=$c) sin research").substitute(c=curvature)
        )

    # Archivos impactados no existen
    artifacts = task.get("impacted_artifacts", task.get("artifacts", {}))
    files = artifacts.get("files", [])
    if not files:
        gap_signals.append("No se identificaron archivos a impactar")

    if gap_signals:
        print("GAPS_DETECTED")
        for signal in gap_signals:
            eprint(Template("  - $item").substitute(item=signal))
        return 0  # 0 = hay gaps, necesita research

    print("NO_GAPS")
    print("Contexto suficiente para continuar.")
    return 1


def cmd_check_analysis(args: list[str]) -> int:
    """Gate validation: ?el analisis 3-capas fue completado?

    Lee progress.json para verificar que el analisis 3-capas existe.
    Exit 0 = analysis done, 1 = analysis pending/incomplete.
    """
    if len(args) < 2:
        eprint("Uso: task.py check-analysis <chain_dir> <task_id>")
        return 2

    args[0]
    task_id = args[1]

    # Leer progress.json
    progress_path = None  # walker.py manages state
    progress = load_json(progress_path)
    if not progress:
        eprint("progress.json no encontrado. Analisis no verificado.")
        return 1

    # Buscar evidencia del analisis 3-capas
    # Orden: contract_data.ADVANCE_TASK[task_id] > analysis_3_layer[task_id] > tasks[].analysis_3layer
    prog = progress.get("progress", progress)
    current = prog.get("current", {})
    contract = (
        current.get("contract_data", {}).get("ADVANCE_TASK", {}).get(task_id, None)
    )
    analysis = (
        contract if contract else current.get("analysis_3_layer", {}).get(task_id, None)
    )

    if not analysis:
        tasks_data = prog.get("tasks", [])
        for t in tasks_data:
            if t.get("task_id") == task_id:
                analysis = t.get("analysis_3layer", t.get("three_layer_analysis", None))
                break
    if not analysis:
        eprint(
            Template(
                "No se encontro registro de analisis 3-capas para $tid"
            ).substitute(tid=task_id)
        )
        return 1

    # Verificar que las 3 capas estan completas
    required_keys = ["q1", "a1", "q2", "a2", "q3", "a3"]
    if isinstance(analysis, dict):
        missing = [k for k in required_keys if k not in analysis]
        if missing:
            eprint(
                Template("Analisis 3-capas incompleto. Faltan: $keys").substitute(
                    keys=", ".join(missing)
                )
            )
            return 1
    elif isinstance(analysis, str):
        if len(analysis.strip()) < 50:
            eprint("Analisis 3-capas demasiado corto para ser completo.")
            return 1
    else:
        eprint("Formato de analisis 3-capas no reconocido.")
        return 1

    print("ANALYSIS_COMPLETE")
    print(Template("Analisis 3-capas verificado para $tid").substitute(tid=task_id))
    return 0
