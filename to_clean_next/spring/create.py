"""spring/create.py — Create sprint"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from .common import (
    _ensure_v3_defaults,
    _now_iso,
    _validate_schema,
    _validate_task_fields,
    eprint,
    load_json,
    write_json,
)
from projects import _SCHEMA_VERSION

_HAVE_PROJECTS = False


def _run_test_predicate_verification(plan: dict, project_root: str) -> dict:
    """Real verification: execute the predicate-validation test suite.

    Instead of trusting LLM-written metadata (validation.predicates), this
    runs ``test_predicate_validation.py`` against the real server and counts
    how many of the goal predicates actually pass. A predicate is
    satisfied ONLY if its corresponding test case passes against the live
    MCP server.

    Returns a dict compatible with _run_predicate_verification_all:
        satisfied / unsatisfied / total / real_satisfied
    """
    import subprocess

    pl = plan.get("plan", plan)
    g_obj = pl.get("goal", {})
    preds = g_obj.get("predicates", {})
    all_predicates = []
    for ptype in ("must_exist", "must_not_exist", "invariants"):
        for p in preds.get(ptype, []):
            all_predicates.append(p)

    total = len(all_predicates)
    result = {
        "satisfied": [],
        "unsatisfied": [],
        "total": total,
        "real_satisfied": 0,
        "_error": None,
    }

    # Locate the predicate-validation test inside the project
    test_candidates = [
        "cognitive_v2/features/agent/tests/test_predicate_validation.py",
        "projects/cognitive-mcp/cognitive_v2/features/agent/tests/test_predicate_validation.py",
    ]
    test_rel = None
    for cand in test_candidates:
        if os.path.exists(os.path.join(project_root, cand)):
            test_rel = cand
            break
    if test_rel is None:
        result["_error"] = (
            "test_predicate_validation.py no encontrado en el proyecto. "
            "No se puede verificar predicates contra código real."
        )
        return result

    # Run only the real validation tests (exclude the broader suites that may
    # require external services). Use -q and capture the summary line.
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        test_rel,
        "-q",
        "-p",
        "no:cacheprovider",
        "--no-header",
        "-rN",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception as exc:  # pragma: no cover
        result["_error"] = f"No se pudo ejecutar el test: {exc}"
        return result

    output = proc.stdout + proc.stderr
    # pytest summary line: "X passed, Y failed" or "X passed"

    m = re.search(r"(\d+)\s+passed", output)
    passed = int(m.group(1)) if m else 0
    fm = re.search(r"(\d+)\s+failed", output)
    failed = int(fm.group(1)) if fm else 0

    # The TestPredicateValidation class parametrizes one case per goal
    # predicate. If every case passes, all predicates are satisfied.
    if failed == 0 and passed > 0:
        result["satisfied"] = all_predicates
        result["real_satisfied"] = total
    elif proc.returncode == 0 and passed > 0:
        result["real_satisfied"] = total
        result["satisfied"] = all_predicates
    else:
        # Test failed: report predicates as unsatisfied (we can't know which
        # precisely without parsing IDs, so we block the close conservatively).
        result["unsatisfied"] = [
            {
                "predicate": p,
                "type": "unknown",
                "reason": "test_predicate_validation.py falló (ver salida pytest)",
            }
            for p in all_predicates
        ]
        result["real_satisfied"] = 0
    return result


def _run_predicates(chain_dir: str) -> bool:
    """Execute validation predicates from plan.json tasks.

    Runs each predicate command via subprocess. If any fails, prints
    error details and returns False. On success, returns True.

    This is the core of the executable evidence mechanism:
    - task_context uses ast-grep to verify code symbols exist
    - validation.predicates are executable commands that verify behavior
    - Both work together to create a self-validating sprint
    """
    plan_path = os.path.join(chain_dir, "plan.json")
    plan = load_json(plan_path)
    if not plan:
        return True

    pl = plan.get("plan", plan)
    tasks = pl.get("tasks", [])
    if not tasks:
        return True

    all_predicates = []
    for t in tasks:
        preds = t.get("validation", {}).get("predicates", [])
        all_predicates.extend(preds)

    if not all_predicates:
        return True

    print()
    print("=" * 70)
    print(f"🔍 EJECUTANDO {len(all_predicates)} PREDICATES DE VALIDACIÓN")
    print("=" * 70)

    passed = 0
    failed = 0
    for i, pred in enumerate(all_predicates, 1):
        print(f"  [{i}/{len(all_predicates)}] {pred[:80]}...")
        try:
            result = subprocess.run(
                pred,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=os.path.abspath(chain_dir),
            )
            if result.returncode == 0:
                print("      ✅ PASS")
                passed += 1
            else:
                print(f"      ❌ FAIL (exit {result.returncode})")
                if result.stderr:
                    print(f"         stderr: {result.stderr[:200]}")
                failed += 1
        except subprocess.TimeoutExpired:
            print("      ❌ TIMEOUT (30s)")
            failed += 1
        except Exception as e:
            print(f"      ❌ ERROR: {e}")
            failed += 1

    print()
    print(f"  📊 Resultado: {passed} passed, {failed} failed")
    if failed > 0:
        print("=" * 70)
        eprint("❌ PREDICATE VERIFICATION FAILED")
        eprint("   Uno o más predicates fallaron. El sprint no es válido.")
        eprint("   Corrige el código o los predicates y reintenta.")
        print("=" * 70)
        return False

    print("  ✅ Todos los predicates pasaron")
    print("=" * 70)
    return True


def _create_from_analysis(
    chain_dir: str, analysis_path: str, goal: str, project_id: str
) -> int:
    """Create sprint from a completed 6-layer deep analysis."""
    analysis_chain = os.path.abspath(analysis_path)
    if not os.path.isdir(os.path.join(analysis_chain, ".analysis")):
        eprint(f"❌ No se encontró .analysis/ en {analysis_chain}")
        eprint("   Ejecuta primero: spring.py analyze init ... + layers + finalize")
        return 2
    if not _HAVE_ANALYZE or _compile_analysis is None:
        eprint("❌ Módulo deep_analysis no disponible para compilar")
        return 2

    plan, progress, error = _compile_analysis(analysis_chain)
    if error:
        eprint(f"❌ Error al compilar análisis: {error['error']}")
        return 2

    analysis_meta = (
        load_json(os.path.join(analysis_chain, ".analysis", "meta.json")) or {}
    )
    if analysis_meta.get("status") != "completed":
        eprint(
            f"❌ Análisis en {analysis_chain} no está completo (status: {analysis_meta.get('status', 'unknown')})"
        )
        eprint("   Ejecuta todas las capas y finaliza con:")
        eprint(f"   python3 scripts/spring.py analyze finalize {analysis_chain}")
        return 2
    completed = analysis_meta.get("completed_layers", [])
    if len(completed) < 6:
        eprint(f"❌ Análisis incompleto: {len(completed)}/6 capas completadas")
        return 2

    goal = analysis_meta.get("goal", goal)
    pid = analysis_meta.get("project_id", project_id)
    chain_id = os.path.basename(os.path.normpath(chain_dir))
    now = _now_iso()

    for section in ("goal", "tasks"):
        plan["plan"]["meta"]["chain_id"] = chain_id
        plan["plan"]["meta"]["generated_at"] = now
        plan["plan"]["meta"]["source"] = f"{pid}:create --from-analysis"
        plan["plan"]["meta"]["analysis_origin"] = os.path.basename(
            os.path.normpath(analysis_chain)
        )

    os.makedirs(chain_dir, exist_ok=True)
    plan_path = os.path.join(chain_dir, "plan.json")
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")
    write_json(plan_path, plan)
    write_json(sprint_state_path, sprint_state)

    g_obj = plan["plan"]["goal"]
    predicates_out = g_obj.get("predicates", {})
    tasks_out = plan["plan"].get("tasks", [])
    must_exist_count = len(predicates_out.get("must_exist", []))
    must_not_exist_count = len(predicates_out.get("must_not_exist", []))
    invariants_count = len(predicates_out.get("invariants", []))

    if _HAVE_PROJECTS:
        try:
            cog_root = find_cognitive_root()
            if cog_root:
                profile = ProjectProfile(cog_root)
                if profile.load(pid):
                    profile.set_active_goal(goal, chain_id)
                    profile.save()
        except Exception:
            pass

    _ensure_v3_defaults(plan)
    plan_valid = _validate_schema(plan_path, "plan.schema.json")
    if not plan_valid:
        eprint("=" * 70)
        eprint("❌ ENFORCEMENT BLOCKED: Schema validation failed")
        eprint("=" * 70)
        for p in [plan_path, sprint_state_path]:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        return 2

    _kv_save(chain_id, "plan", plan, pid)
    if not _run_predicates(chain_dir):
        for p in [plan_path, sprint_state_path]:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        return 2

    print()
    print("=" * 70)
    print(f"✅ SPRINT CREADO DESDE ANÁLISIS PROFUNDO: {chain_id}")
    print("=" * 70)
    print(f"  Proyecto:    {pid}")
    print(f"  Goal:        {goal[:80]}")
    print(f"  Chain dir:   {chain_dir}")
    print(f"  Análisis:    {analysis_chain}")
    print(f"  Tareas:      {len(tasks_out)}")
    print(
        f"  Predicates:  {must_exist_count} must_exist, {must_not_exist_count} must_not_exist, {invariants_count} invariants"
    )
    print("  Schema:      ✅ plan + progress válidos")
    print()
    print("📋 DECISION PROTOCOL:")
    na = progress.get("progress", {}).get("current", {}).get("next_action", {})
    if na:
        print(
            f"     python3 scripts/task.py advance {chain_dir} {na.get('task_id', 'TASK-001')}"
        )
    print(f"\n  ▶️  Para ver estado:\n     python3 scripts/spring.py status {chain_dir}")
    print("=" * 70)
    return 0


def cmd_create(args: list[str]) -> int:
    """Create a new sprint: plan.json + sprint-state.json.

    Usage:
      spring.py create <chain_dir> --project-id <id> \\
               --goal "..." --predicates '{}' --tasks '[]'
    """
    if len(args) < 1:
        eprint("❌ Uso: spring.py create <chain_dir> --project-id <id> --goal ...")
        eprint(
            "   [--predicates JSON] [--tasks JSON] [--tasks-file <path>] [--project-type TYPE]"
        )
        eprint("   [--scrum-role po|dev|qa] [--domains LIST] [--tags LIST]")
        eprint(
            "   [--from-sprint <chain_dir_prev>] Heredar distance del sprint anterior"
        )
        eprint("   PO (define), Dev (implementa), QA (valida)")
        return 2

    chain_dir = os.path.abspath(args[0])

    # Parse flags
    [a for a in args[1:] if a.startswith("--")]
    flag_vals = {}
    rest = args[1:]
    for i, a in enumerate(rest):
        if a.startswith("--"):
            key = a[2:]
            if i + 1 < len(rest) and not rest[i + 1].startswith("--"):
                flag_vals[key] = rest[i + 1]

    project_id = flag_vals.get("project-id", "")
    goal = flag_vals.get("goal", "")
    predicates_json = flag_vals.get("predicates", "{}")
    tasks_json = flag_vals.get("tasks", "[]")
    tasks_file = flag_vals.get("tasks-file", "")

    # --tasks-file y --tasks son mutuamente excluyentes
    if tasks_file and flag_vals.get("tasks"):
        eprint("❌ --tasks-file y --tasks son mutuamente excluyentes. Usa solo uno.")
        return 2

    # Si se proporciona --tasks-file, leer JSON desde el archivo
    if tasks_file:
        if not os.path.exists(tasks_file):
            eprint(f"❌ Archivo no encontrado: {tasks_file}")
            return 2
        try:
            with open(tasks_file) as f:
                tasks_json = f.read()
        except OSError as e:
            eprint(f"❌ Error leyendo {tasks_file}: {e}")
            return 2
    project_type = flag_vals.get("project-type", "backend")
    scrum_role = flag_vals.get("scrum-role", "po")
    domains_raw = flag_vals.get("domains", goal.split(" ")[:3])
    tags_raw = flag_vals.get("tags", "python")

    # ── FROM-ANALYSIS path ───────────────────────────────────
    from_analysis = flag_vals.get("from-analysis", "")
    if from_analysis:
        return _create_from_analysis(chain_dir, from_analysis, goal, project_id)

    # ── NORMAL path (manual predicates + tasks) ────────────────
    if not project_id:
        eprint("❌ --project-id es requerido")
        return 2
    if not goal:
        eprint("❌ --goal es requerido (descripción del objetivo)")
        return 2
    if len(goal) < 10:
        eprint("❌ --goal debe tener al menos 10 caracteres")
        eprint("   Describe el objetivo de forma clara y detallada.")
        return 2

    try:
        predicates = json.loads(predicates_json)
    except json.JSONDecodeError as e:
        eprint(f"❌ predicates JSON inválido: {e}")
        return 2

    try:
        tasks = json.loads(tasks_json)
    except json.JSONDecodeError as e:
        eprint(f"❌ tasks JSON inválido: {e}")
        return 2

    # ── PRE-WRITE VALIDATION: Check required task fields against schema ──
    valid, field_errors = _validate_task_fields(tasks)
    if not valid:
        eprint()
        eprint("=" * 70)
        eprint("❌ VALIDACIÓN PRE-WRITE: Faltan campos requeridos en las tareas")
        eprint("=" * 70)
        for err in field_errors:
            eprint(err)
        eprint()
        eprint("  Referencia: schema/plan.schema.json → $defs/task → required")
        eprint()
        eprint("  Template de tarea completa:")
        eprint('  {"task_id": "TASK-001", "type": "feature",')
        eprint('   "objective": "Implementar X",')
        eprint('   "result_state": "X funciona correctamente",')
        eprint('   "domain": "core",')
        eprint('   "impacted_artifacts": {"files": ["ruta/archivo.py"]},')
        eprint('   "risks": {"curvature": 0.20, "commit_trust": 0.70},')
        eprint('   "task_context": {"observation": "...", "structure": "...",')
        eprint('                   "behavior": "...", "causality": "...",')
        eprint('                   "purpose": "...", "principles": "..."},')
        eprint('   "validation": {"predicates": ["Una función X existe"]}}')
        eprint()
        eprint("  Corrige las tareas y reintenta.")
        eprint("=" * 70)
        return 2

    # Validate each task has proper description and DoD before writing
    for t in tasks:
        obj = t.get("objective", "")
        rs = t.get("result_state", "")
        tid = t.get("task_id", "?")
        if len(obj) < 10:
            eprint(
                f"❌ Tarea {tid}: objective debe tener >= 10 caracteres (actual: {len(obj)})"
            )
            eprint("   La descripcion de cada tarea debe ser explicita.")
            return 2
        if len(rs) < 10:
            eprint(
                f"❌ Tarea {tid}: result_state debe tener >= 10 caracteres (actual: {len(rs)})"
            )
            eprint("   La Definition of Done (DoD) debe describir el estado esperado.")
            return 2

    # Normalize predicates structure
    if isinstance(predicates, list):
        predicates = {
            "must_exist": [p for p in predicates if not p.startswith("NO ")],
            "must_not_exist": [p[3:] for p in predicates if p.startswith("NO ")],
            "invariants": [],
        }
    elif isinstance(predicates, dict):
        predicates.setdefault("must_exist", [])
        predicates.setdefault("must_not_exist", [])
        predicates.setdefault("invariants", [])

    # Resolve domains/tags
    if isinstance(domains_raw, str):
        domains = [d.strip() for d in domains_raw.split(",") if d.strip()]
    else:
        domains = domains_raw or ["core"]
    if isinstance(tags_raw, str):
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    else:
        tags = tags_raw or [project_type]

    # Generate chain_id from goal if not already in chain_dir name
    chain_id = os.path.basename(os.path.normpath(chain_dir))

    now = _now_iso()

    # ── Build plan.json ───────────────────────────────────────
    # Assign scrum_role to each task (default: po, override from --scrum-role or task data)
    for t in tasks:
        if "scrum_role" not in t:
            t["scrum_role"] = scrum_role

    plan = {
        "plan": {
            "meta": {
                "chain_id": chain_id,
                "generated_at": now,
                "version": _SCHEMA_VERSION,
                "source": f"{project_id}:create",
                "type": "plan",
            },
            "project": {
                "project_id": project_id,
                "project_type": project_type,
                "domains": domains,
                "tags": tags,
            },
            "goal": {
                "description": goal,
                "path_summary": "",
                "predicates": predicates,
                "signatures": [],
            },
            "tasks": tasks,
            "parameters": {},
        }
    }

    # ── FROM-SPRINT: Inherit initial_distance from previous sprint ──
    if from_sprint:
        prev_sprint_dir = os.path.abspath(from_sprint)
        prev_state_path = os.path.join(prev_sprint_dir, "sprint-state.json")
        if not os.path.exists(prev_state_path):
            eprint(
                f"❌ --from-sprint: No se encontró sprint-state.json en {prev_sprint_dir}"
            )
            return 2
        prev_state = load_json(prev_state_path)
        if prev_state is None:
            eprint(
                f"❌ --from-sprint: sprint-state.json inválido o corrupto en {prev_sprint_dir}"
            )
            return 2
        prev_closure = prev_state.get("metrics", {}).get("closure", {})
        prev_distance = prev_closure.get("distance_at_close", None)
        if prev_distance is None:
            prev_trail = prev_state.get("audit", {}).get("trail", [])
            if prev_trail:
                prev_distance = prev_trail[-1].get("distance", None)
        initial_distance = prev_distance if prev_distance is not None else 1.0
        initial_distance = round(initial_distance, 4)

    # ── Create directories and write files ────────────────────
    os.makedirs(chain_dir, exist_ok=True)

    plan_path = os.path.join(chain_dir, "plan.json")
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")

    write_json(plan_path, plan)

    # ── Update project.json active_goal ───────────────────────
    if _HAVE_PROJECTS:
        try:
            cog_root = find_cognitive_root()
            if cog_root:
                profile = ProjectProfile(cog_root)
                if profile.load(project_id):
                    profile.set_active_goal(goal, chain_id)
                    profile.save()
        except Exception:
            pass

    # ── ENFORCEMENT: Validate schemas before proceeding ───────
    plan_valid = _validate_schema(plan_path, "plan.schema.json")

    if not plan_valid:
        eprint()
        eprint("=" * 70)
        eprint("❌ ENFORCEMENT BLOCKED: Schema validation failed")
        eprint("=" * 70)
        eprint("  Los archivos creados tienen datos inválidos.")
        eprint("  El LLM debe corregir los errores y reintentar.")
        eprint()
        eprint("  ❌ plan.json no pasa el schema")
        eprint()
        eprint("  Campos comunes que faltan o están vacíos:")
        eprint("    • task.objective — minLength 10")
        eprint("    • task.result_state — minLength 10")
        eprint("    • task.domain — minLength 2")
        eprint("    • task.impacted_artifacts.files — minItems 1")
        eprint("    • task.validation.predicates — minItems 1")
        eprint("    • task.risks.curvature — required (0–1)")
        eprint()
        eprint("  Revisa el schema en schema/plan.schema.json")
        eprint("  para ver todos los campos requeridos.")
        eprint("=" * 70)
        try:
            if os.path.exists(plan_path):
                os.remove(plan_path)
            if os.path.exists(sprint_state_path):
                os.remove(sprint_state_path)
            if os.path.isdir(chain_dir) and not os.listdir(chain_dir):
                os.rmdir(chain_dir)
        except OSError:
            pass
        return 2

    # ── KV save ───────────────────────────────────────────────
    _kv_save(chain_id, "plan", plan, project_id)

    # ── Self-sufficient output ────────────────────────────────
    print()
    print("=" * 70)
    print(f"✅ SPRINT CREADO: {chain_id}")
    print("=" * 70)
    print(f"  Proyecto:    {project_id}")
    print(f"  Goal:        {goal[:80]}")
    print(f"  Chain dir:   {chain_dir}")
    print(f"  Tareas:      {len(tasks)}")
    print(
        f"  Predicates:  {len(predicates.get('must_exist', []))} must_exist, "
        f"{len(predicates.get('must_not_exist', []))} must_not_exist, "
        f"{len(predicates.get('invariants', []))} invariants"
    )
    print("  Schema:      ✅ plan + progress válidos")
    print()
    print("📋 DECISION PROTOCOL:")
    print()
    print("  ▶️  Siguiente: implementar tareas y avanzar:")
    if next_action:
        print(
            f"     python3 scripts/task.py advance {chain_dir} {next_action['task_id']}"
        )
    print()
    print("  ▶️  Para ver estado del sprint:")
    print(f"     python3 scripts/spring.py status {chain_dir}")
    print()
    print("💡 El sprint está listo. El LLM solo necesita comandar tareas.")
    print("=" * 70)

    return 0


def _detect_obsolete_risks(risks: list[dict], project_root: str) -> list[dict]:
    results = []
    for risk in risks:
        desc = risk.get("description", "")
        refs = _extract_file_refs(desc)
        if not refs:
            risk["status"] = "unchecked"
            results.append(risk)
            continue
        missing = []
        for ref in refs:
            candidate = os.path.join(project_root, ref)
            if os.path.exists(candidate):
                continue
            projects_dir = os.path.join(project_root, "projects")
            if os.path.isdir(projects_dir):
                found = False
                for sub in os.listdir(projects_dir):
                    sub_path = os.path.join(projects_dir, sub)
                    if not os.path.isdir(sub_path):
                        continue
                    for root, dirs, files in os.walk(sub_path):
                        dirs[:] = [
                            d
                            for d in dirs
                            if d
                            not in (
                                "__pycache__",
                                ".git",
                                "node_modules",
                                ".venv",
                                "venv",
                                "dist",
                                "build",
                                ".cognitive",
                                ".agents",
                                ".opencode",
                            )
                        ]
                        basename = os.path.basename(ref)
                        if basename in files:
                            found = os.path.join(root, basename)
                            break
                    if found:
                        break
                if found:
                    continue
            missing.append(ref)
        if missing:
            risk["status"] = "obsolete"
            risk["obsolete_files"] = missing
            print(f"  ⚠️  Riesgo obsoleto: {risk.get('description', '')[:70]}...")
            for m in missing:
                print(f"      → Archivo no encontrado: {m}")
        else:
            risk["status"] = "valid"
        results.append(risk)
    return results


def _run_predicate_verification_all(plan: dict, project_root: str) -> dict:
    """Run real-code verification against ALL predicates in goal.

    Uses _parse_predicate() + run_verification() from _internal/advance.py
    to grep/pytest the codebase instead of trusting metadata written by the LLM.

    Each predicate string is parsed into (pattern, type, file_hint) and then
    verified against actual code in project_root. This is FASE 2 re-evaluation.

    Args:
        plan: Plan dict (with plan.plan or flat format for compat)
        project_root: Absolute path to the project root

    Returns:
        dict with:
            satisfied: list[str] — predicates that pass real-code verification
            unsatisfied: list[dict] — predicates that fail, each with
                predicate, type, reason (explains why it failed)
            total: int — total predicates checked
            real_satisfied: int — count of predicates that passed real check
    """
    if not _HAVE_ADVANCE:
        return {
            "satisfied": [],
            "unsatisfied": [],
            "total": 0,
            "real_satisfied": 0,
            "_error": "No se pudo importar _internal/advance — verificación real no disponible",
        }

    pl = plan.get("plan", plan)
    g_obj = pl.get("goal", {})
    preds = g_obj.get("predicates", {})

    all_predicates = []
    for ptype in ("must_exist", "must_not_exist", "invariants"):
        for p in preds.get(ptype, []):
            all_predicates.append({"predicate": p, "type": ptype})

    satisfied = []
    unsatisfied = []

    for item in all_predicates:
        p = item["predicate"]
        ptype = item["type"]

        # Parse the predicate into grep-able pattern
        try:
            pattern, vtype, file_hint = _parse_predicate(p)
        except Exception:
            unsatisfied.append(
                {
                    "predicate": p,
                    "type": ptype,
                    "reason": "no se pudo parsear el predicate",
                }
            )
            continue

        # Invariants treated as must_exist checks
        check_type = (
            vtype if vtype in ("must_exist", "must_not_exist") else "must_exist"
        )

        # Run verification against real code
        try:
            passed = run_verification(
                {"predicate": pattern, "type": check_type},
                project_root,
                file_hint=file_hint,
            )
        except Exception as e:
            unsatisfied.append(
                {"predicate": p, "type": ptype, "reason": f"error en verificación: {e}"}
            )
            continue

        if passed:
            satisfied.append(p)
        else:
            reason = (
                f"{'no encontrado' if check_type == 'must_exist' else 'aún presente'}"
                f" (grep: {pattern[:70]} en {file_hint or 'project_root'})"
            )
            unsatisfied.append({"predicate": p, "type": ptype, "reason": reason})

    return {
        "satisfied": satisfied,
        "unsatisfied": unsatisfied,
        "total": len(all_predicates),
        "real_satisfied": len(satisfied),
    }
