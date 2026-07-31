#!/usr/bin/env python3
"""
task/code_review_scan.py — CODE_REVIEW_SCAN wrapper.

Lee impacted_artifacts.files de la tarea en plan.json y pasa SOLO
esos archivos a analyze_refactored.py con --json, evaluando:
  a) Quality gates globales (umbrales via analyze_refactored.py — Option A)
  b) Quality_gates especificos de la tarea (desde plan.json — Option B)

Exit codes:
    0 — todos los quality gates pasan
    1 — uno o mas quality gates fallaron (activa on_failure_loop)
    2 — error (no se pudo leer plan.json o tarea no encontrada)
"""

import json
import os
import subprocess
import sys

# ── Reusar logica de _internal/qlty_check.py (DRY) ──────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_INTERNAL_DIR = os.path.join(_THIS_DIR, "..", "_internal")
if _INTERNAL_DIR not in sys.path:
    sys.path.insert(0, _INTERNAL_DIR)

from qlty_check import get_files_from_task, find_project_root


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def _resolve_file(rel_path: str, search_bases: list[str]) -> str | None:
    """Busca un archivo en multiples bases. Retorna la primera que existe.

    Tambien maneja el caso donde rel_path termina en .py pero es un
    package (directorio): busca la version directorio y __init__.py.
    """
    for base in search_bases:
        candidate = os.path.join(base, rel_path)
        if os.path.isfile(candidate):
            return candidate
        if rel_path.endswith(".py"):
            dir_candidate = candidate[:-3]
            if os.path.isdir(dir_candidate):
                init_py = os.path.join(dir_candidate, "__init__.py")
                if os.path.isfile(init_py):
                    return init_py
                return dir_candidate
    return None


def _log_file_warning(rel_path: str, search_bases: list[str]) -> None:
    """Log detallado de por que no se encontro un archivo."""
    eprint(f"⚠️  Archivo no encontrado: {rel_path}")
    eprint("   Bases buscadas:")
    for base in search_bases:
        candidate = os.path.join(base, rel_path)
        exists = os.path.exists(candidate)
        eprint(f"     {'✅' if exists else '❌'} {candidate}")
    eprint()


def _get_delta_skill_base(project_root: str) -> str | None:
    """Encuentra la base del delta skill (.claude/skills/delta/)."""
    candidates = [
        os.path.join(project_root, ".claude", "skills", "delta"),
        os.path.join(project_root, "..", ".claude", "skills", "delta"),
    ]
    for c in candidates:
        norm = os.path.normpath(c)
        if os.path.isdir(norm):
            return norm
    return None


def _load_task_quality_gates(chain_dir: str, task_id: str) -> list[str]:
    """Carga los quality_gates especificos de la tarea desde plan.json."""
    plan_path = os.path.join(chain_dir, "plan.json")
    if not os.path.isfile(plan_path):
        eprint(f"⚠️  No se encontro plan.json en {chain_dir}")
        return []
    try:
        with open(plan_path) as f:
            plan = json.load(f)
        tasks = plan.get("plan", plan).get("tasks", [])
        for t in tasks:
            tid = t.get("task_id") or t.get("id", "")
            if tid == task_id:
                return t.get("quality_gates", [])
        eprint(f"⚠️  Tarea {task_id} no encontrada en plan.json")
        return []
    except (json.JSONDecodeError, OSError) as e:
        eprint(f"⚠️  Error leyendo plan.json: {e}")
        return []


def _evaluate_task_gates(
    report: dict, quality_gates: list[str]
) -> tuple[bool, list[str]]:
    """Evalua quality_gates especificos de la tarea contra el reporte de analisis.

    Patrones de gate soportados:
      - "<path> pasa lint"                     → verifica 0 errores en ese archivo
      - "No security issues" / "0 security"   → sec_high == 0
      - "Max <N> warnings"                    → total_warnings <= N
      - "tests/... pasa"                      → nota (se verifica en QA_RUN)
      - Cualquier otro                         → pasa con nota informativa
    """
    results: list[str] = []
    all_ok = True

    if not quality_gates:
        return True, []

    for gate in quality_gates:
        gate_lower = gate.lower()

        # Gate: No security issues / 0 security issues
        if "security" in gate_lower and "0" in gate_lower:
            sec_high = 0
            for f in report.get("files", []):
                q = f.get("quality", {}) or {}
                sec_high += q.get("security_high", 0)
            if sec_high > 0:
                all_ok = False
                results.append(f"❌ {gate} — {sec_high} high-severity security issues")
            else:
                results.append(f"✅ {gate}")

        # Gate: Max <N> warnings
        elif gate_lower.startswith("max") and "warning" in gate_lower:
            try:
                max_w = int([w for w in gate_lower.split() if w.isdigit()][0])
            except (IndexError, ValueError):
                continue
            total_warnings = report.get("total_warnings", 0)
            if total_warnings > max_w:
                all_ok = False
                results.append(f"❌ {gate} — {total_warnings} warnings (max {max_w})")
            else:
                results.append(f"✅ {gate} — {total_warnings} warnings")

        # Gate: <path> pasa lint (check file has no errors)
        elif "lint" in gate_lower:
            target_file = gate.split("pasa")[0].strip() if "pasa" in gate else ""
            file_errors = 0
            for f in report.get("files", []):
                if target_file and target_file not in f.get("file", ""):
                    continue
                for c in f.get("checks", []):
                    if c.get("level") == "error":
                        file_errors += 1
            if file_errors > 0:
                all_ok = False
                results.append(f"❌ {gate} — {file_errors} error(es) de lint")
            else:
                results.append(f"✅ {gate}")

        # Gate: test passes (verificado en QA_RUN, solo informativo aqui)
        elif "pasa" in gate_lower and "test" in gate_lower:
            results.append(f"ℹ️  {gate} — se verificara en QA_RUN")

        # Gate generico: pasa con nota informativa
        else:
            results.append(
                f"ℹ️  {gate} — (evaluacion no disponible para este tipo de gate)"
            )

    return all_ok, results


def main() -> int:
    if len(sys.argv) < 3:
        eprint(f"Uso: {sys.argv[0]} <chain_dir> <task_id>")
        eprint("  chain_dir  Ruta al directorio del sprint (contiene plan.json)")
        eprint("  task_id    ID de la tarea (ej: TASK-002)")
        return 2

    chain_dir = os.path.abspath(sys.argv[1])
    task_id = sys.argv[2]
    project_root = find_project_root(chain_dir)
    skill_base = _get_delta_skill_base(project_root) or project_root

    DEFAULT_TIMEOUT = 180

    # Bases de busqueda, en orden de prioridad
    search_bases: list[str] = []
    if skill_base:
        search_bases.append(os.path.join(skill_base, "scripts"))
        search_bases.append(skill_base)
        search_bases.append(os.path.join(skill_base, "scripts", "_internal"))
    search_bases.append(project_root)

    # ── 1. Leer archivos impactados de la tarea ──
    files = get_files_from_task(chain_dir, task_id)

    if not files:
        eprint("⚠️  WARNING: No se encontraron impacted_artifacts.files para")
        eprint(f"   la tarea {task_id}. Usando git diff como fallback.")
        eprint()
        return _fallback_scan_all(project_root, DEFAULT_TIMEOUT)

    # ── 2. Resolver cada archivo contra las bases de busqueda ──
    existing: list[str] = []
    missing: list[str] = []

    for f in files:
        if os.path.isabs(f) and os.path.isfile(f):
            existing.append(f)
            continue
        resolved = _resolve_file(f, search_bases)
        if resolved:
            existing.append(resolved)
        else:
            missing.append(f)

    for f in missing:
        _log_file_warning(f, search_bases)

    if not existing:
        eprint("⚠️  Ningun archivo impactado existe en disco.")
        eprint("   Usando git diff como fallback.")
        eprint()
        return _fallback_scan_all(project_root, DEFAULT_TIMEOUT)

    # ── 3. Ejecutar analyze_refactored.py con --json para parsear resultado ──
    eprint(f"🔍 CODE_REVIEW_SCAN: {len(existing)} archivo(s) impactados por {task_id}")
    for f in existing:
        rel = os.path.relpath(f, project_root)
        eprint(f"   - {rel}")
    eprint()

    analyze_script = os.path.join(_THIS_DIR, "..", "analyze_refactored.py")

    result = subprocess.run(
        [sys.executable, analyze_script, "--json"] + existing,
        capture_output=True,
        text=True,
        cwd=project_root,
        timeout=DEFAULT_TIMEOUT,
    )

    analyze_exit = result.returncode

    # ── 4. Parsear JSON output ──
    report = None
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        eprint("⚠️  No se pudo parsear el output JSON de analyze_refactored.py")
        eprint(f"   stdout: {result.stdout[:500]}")
        # Fallback al exit code de analyze (Option A ya lo maneja)
        return analyze_exit

    # Summary visible para el operador
    total_issues = report.get("total_checks", 0)
    total_errors = report.get("total_errors", 0)
    total_warnings = report.get("total_warnings", 0)
    total_smells = report.get("total_smells", 0)
    eprint(
        f"📊 Reporte: {report.get('files_analyzed', 0)} archivos, "
        f"{total_issues} issues ({total_errors} errors, {total_warnings} warnings), "
        f"{total_smells} smells"
    )

    # ── 5. Evaluar quality_gates especificos de la tarea (Option B) ──
    quality_gates = _load_task_quality_gates(chain_dir, task_id)
    if quality_gates:
        eprint(
            f"\n📋 Evaluando {len(quality_gates)} quality gates de la tarea {task_id}:"
        )
        gates_ok, gate_results = _evaluate_task_gates(report, quality_gates)
        for gr in gate_results:
            eprint(f"   {gr}")
        eprint()

        if not gates_ok:
            eprint(
                "❌ Task-specific quality gates FAILED — on_failure_loop se activara"
            )
            eprint()
            return 1
    else:
        eprint("ℹ️  No hay quality_gates definidos para esta tarea en plan.json")

    # ── 6. Propagar exit_code de analyze_refactored.py (Option A) ──
    if analyze_exit != 0:
        eprint("❌ Quality thresholds globales excedidos — on_failure_loop se activara")
        eprint()
        return 1

    eprint("✅ CODE_REVIEW_SCAN: todos los quality gates pasaron")
    eprint()
    return 0


def _fallback_scan_all(project_root: str, timeout: int) -> int:
    """Fallback: escanear proyecto completo con --json."""
    eprint("▶️  Fallback: git diff --name-only 2>&1")
    try:
        r = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=project_root,
        )
        if r.returncode == 0 and r.stdout.strip():
            changed = r.stdout.strip().split("\n")
            eprint(f"   {len(changed)} archivos modificados detectados:")
            for cf in changed[:15]:
                eprint(f"   - {cf}")
            if len(changed) > 15:
                eprint(f"   ... +{len(changed) - 15} mas")
            eprint()
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass

    eprint("=" * 70)
    result = subprocess.run(
        [
            sys.executable,
            "projects/delta-skill/scripts/analyze_refactored.py",
            "--json",
            ".",
        ],
        capture_output=True,
        text=True,
        cwd=project_root,
        timeout=timeout,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
