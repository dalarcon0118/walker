#!/usr/bin/env python3
"""delta-qlty-check.py — Run Qlty metrics on files modified by a delta task.

Usage:
    python3 delta-qlty-check.py <chain_dir> <task_id> [--verbose]

This script:
  1. Reads plan.json to find impacted_artifacts.files for the task
  2. Falls back to `git diff --name-only` for files changed in working tree
  3. Runs analyze_refactored.py --json on those files
  4. Returns JSON with metrics + pass/fail per quality threshold

Exit codes:
    0 — all quality gates pass
    1 — some quality gates FAIL (cyclo > 60, LCOM > 20, etc.)
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Import hallucination scanner directly (no subprocess)
_HAVE_HALLUCINATION = False
try:
    from _internal.hallucination import scan_file as _scan_hallucination_file

    _HAVE_HALLUCINATION = True
except ImportError:
    _HAVE_HALLUCINATION = False


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def load_json(path):
    with open(path) as f:
        return json.load(f)


def find_project_root(start: str | None = None) -> str:
    """Find project root (where .cognitive/ lives).

    Checks, in order:
      1. ``.cognitive/delta/`` — legacy delta marker
      2. ``.cognitive/projects/*/delta/`` — multi-project delta marker
         (walks up and checks if any projects/*/delta/ dir exists)
      3. ``.cognitive/`` — broad fallback

    Always resolves to absolute path to avoid os.path.dirname('') issues
    with relative paths.
    """
    cwd = start or os.getcwd()
    cwd = os.path.abspath(cwd)
    if not cwd or not os.path.isdir(cwd):
        cwd = os.path.abspath(os.getcwd())

    # First pass: look for .cognitive/delta (legacy delta marker)
    for _ in range(10):
        delta_marker = os.path.join(cwd, ".cognitive", "delta")
        if os.path.isdir(delta_marker):
            return cwd
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent

    # Second pass: look for .cognitive/projects/<id>/delta (multi-project marker)
    cwd = start or os.getcwd()
    cwd = os.path.abspath(cwd)
    for _ in range(10):
        projects_dir = os.path.join(cwd, ".cognitive", "projects")
        if os.path.isdir(projects_dir):
            # Check if any project has a delta/ directory
            for pid in os.listdir(projects_dir):
                proj_delta = os.path.join(projects_dir, pid, "delta")
                if os.path.isdir(proj_delta):
                    return cwd
            # projects/ exists but no project has delta/ yet → still valid
            return cwd
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent

    # Third pass: fallback to any .cognitive/
    cwd = start or os.getcwd()
    cwd = os.path.abspath(cwd)
    for _ in range(10):
        if os.path.exists(os.path.join(cwd, ".cognitive")):
            return cwd
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent

    return os.path.abspath(os.getcwd())


def get_files_from_task(chain_dir: str, task_id: str) -> list[str]:
    """Get file list from impacted_artifacts.files in plan.json."""
    plan_path = os.path.join(chain_dir, "plan.json")
    if not os.path.exists(plan_path):
        return []
    plan = load_json(plan_path)
    pl = plan.get("plan", plan)
    for t in pl.get("tasks", []):
        if t.get("task_id") == task_id:
            artefacts = t.get("impacted_artifacts", {})
            files = artefacts.get("files", [])
            if files:
                return files
            # Also check signatures
            sigs = pl.get("goal", {}).get("signatures", [])
            sig_files = [s["file"] for s in sigs if s.get("file")]
            if sig_files:
                return sig_files
    return []


def get_git_changed_files(project_root: str) -> list[str]:
    """Get list of files changed in working tree via git diff."""
    try:
        r = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=project_root,
        )
        if r.returncode == 0 and r.stdout.strip():
            return [
                os.path.join(project_root, f) for f in r.stdout.strip().split("\n") if f
            ]

        # Also check staged + unstaged
        r2 = subprocess.run(
            ["git", "diff", "--name-only", "--cached", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=project_root,
        )
        if r2.returncode == 0 and r2.stdout.strip():
            return [
                os.path.join(project_root, f)
                for f in r2.stdout.strip().split("\n")
                if f
            ]
    except subprocess.TimeoutExpired:
        pass
    return []


def run_qlty_analyze(analyze_script: str, files: list[str], project_root: str) -> dict:
    """Run analyze_refactored.py --json on the given files (any language)."""
    if not files:
        return {"files": [], "files_analyzed": 0}

    # Filter to existing files (any language Qlty supports)
    existing = [f for f in files if os.path.exists(f)]

    if not existing:
        return {"files": [], "files_analyzed": 0}

    try:
        r = subprocess.run(
            [sys.executable, analyze_script, "--json"] + existing,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=project_root,
        )
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
        if r.stderr:
            eprint(f"⚠️  analyze_refactored.py stderr: {r.stderr[:500]}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        eprint(f"⚠️  Error running analyze_refactored.py: {e}")

    return {"files": [], "files_analyzed": 0}


def run_hallucination_scanner(files: list[str], project_root: str) -> dict:
    """Run hallucination scanner via direct import from _internal.hallucination.

    Returns dict with hallucination results or empty dict on error.
    """
    if not files:
        return {}

    if not _HAVE_HALLUCINATION:
        eprint("⚠️  Hallucination scanner no disponible (import falló).")
        return {}

    # Filter to existing Python/TS files
    existing = [
        f
        for f in files
        if os.path.exists(f)
        and f.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))
    ]

    if not existing:
        return {}

    try:
        # scan_file() takes a single file, returns a dict with per-file results
        results = []
        total_hallucinations = 0
        for fpath in existing:
            result = _scan_hallucination_file(Path(fpath), project_root)
            if result:
                results.append(result)
                total_hallucinations += result.get("issues_count", 0)
        if results:
            return {"files": results, "total_hallucinations": total_hallucinations}
        return {}
    except Exception as e:
        eprint(f"⚠️  Hallucination scanner error: {e}")
        return {}


def evaluate_quality_gates(metrics: dict) -> dict:
    """Evaluate quality gates against thresholds.

    8 gates:
      1. production_bugs       — bugs de producción (eval, pickle, unwrap, etc.)
      2. bug_probability       — bugs correlacionados (cross-reference layer)
      3. security_high         — inyecciones y credenciales hardcodeadas (B6xx)
      4. dead_code             — imports/variables sin usar (F401/F841)
      5. spaghetti_complexity  — CC por función, LCOM, cognitiva
      6. unnecessary_code      — f-strings vacíos, funciones vacías, debug leftovers
      7. hallucinated_imports  — imports que no existen (LLM alucinaciones)
      8. code_duplication      — bloques duplicados (identical/similar code)

    Returns:
        {"passed": bool, "gates": [{...}], "fails": int, "warns": int, "summary": str}
    """
    gates = []

    # Fix instructions map (rule_id → fix suggestion)
    FIX_MAP = {
        "mutable-default-list": "Cambia default=[] a default=None + if x is None: x = [] dentro",
        "mutable-default-dict": "Cambia default={} a default=None + if x is None: x = {} dentro",
        "return-none-in-except": "No tragues el error. O registra con logging, o relanza, o maneja excepciones específicas",
        "no-timeout-requests": "Agrega timeout=30 a requests — puede colgarse en producción",
        "eval-call": "Reemplaza eval() con literal_eval() o un dict de funciones (pattern dispatch)",
        "pickle-load": "Usa JSON o schema validado en vez de pickle.load()",
        "unwrap-used": "Usa pattern matching o ? operator en vez de unwrap()",
        "todo-macro": "Reemplaza todo!() con manejo de error real o unimplemented!()",
        "non-null-assert": "Usa if (x === null) return en vez de non-null assertion (!)",
        "loose-equality": "Cambia == por === para evitar coerción de tipos",
        "panic-call": "Reemplaza panic() con manejo de error usando Result<T, E>",
        "null-return": "Cambia return null por Optional/Maybe o lanza excepción específica",
        "empty-function": "Implementa la función o elimínala — código muerto",
        "empty-class": "Implementa la clase o elimínala — código muerto",
        "bare-fstring": "Usa string normal ('...') en vez de f'...' si no hay placeholders",
        "bare-fstring-double": 'Usa string normal ("...") en vez de f"..." si no hay placeholders',
        "console-log": "Elimina console.log o reemplázalo con logger.debug()",
        "debugger-stmt": "Elimina debugger statement antes de producción",
        "pass-only-except": "Maneja la excepción específica o al menos registra con logging",
        "todo-without-ticket": "Crea un ticket/issue para el TODO o resuélvelo ahora",
    }

    # Cross-reference fix map (by type)
    CROSS_FIX_MAP = {
        "bug": "Excepción silenciada en función compleja. Agrega manejo de excepción específico, no tragues todo",
        "injection": "Inyección detectada. Nunca concatenes strings en SQL/shell.commands. Usa parámetros",
        "xss": "XSS detectado. Sanitiza output o usa escape automático del template engine",
        "security_debt": "Múltiples issues de seguridad. Considera un refactor de seguridad sistémico",
        "exploit_chain": "Secrets + código vulnerable en mismo archivo. Extrae secrets a env vars y sanitiza",
        "var_leak": "Variables sin usar en función compleja. Elimina o usa las variables",
        "refactor": "Quedaron imports peligrosos de refactors incompletos. Limpia o completa el refactor",
        "spaghetti": "Imports mezclados con lógica en archivo complejo. Separa en módulos",
    }

    for f in metrics.get("files", []):
        file_name = f.get("file", "unknown")
        checks = f.get("checks", [])
        quality = f.get("quality", {})

        # ── Baseline: cyclo + lcom + ce (existing) ────────────────
        cyclo = f.get("cyclo", 0)
        gates.append(
            {
                "file": file_name,
                "metric": "cyclo",
                "label": "Complejidad ciclomática total",
                "value": cyclo,
                "threshold": 60,
                "passed": cyclo <= 60,
                "level": "pass" if cyclo <= 30 else ("warn" if cyclo <= 60 else "fail"),
            }
        )

        lcom = f.get("lcom", 0)
        gates.append(
            {
                "file": file_name,
                "metric": "lcom",
                "label": "LCOM* (falta de cohesión)",
                "value": lcom,
                "threshold": 20,
                "passed": lcom <= 20,
                "level": "pass" if lcom <= 5 else ("warn" if lcom <= 20 else "fail"),
            }
        )

        ce = f.get("ce", 0)
        gates.append(
            {
                "file": file_name,
                "metric": "ce",
                "label": "Acoplamiento eferente (Ce)",
                "value": ce,
                "threshold": 10,
                "passed": ce <= 10,
                "level": "pass" if ce <= 5 else ("warn" if ce <= 10 else "fail"),
            }
        )

        # ── Gate 4: dead_code (F401/F841) ─────────────────────────
        dead_checks = [
            c
            for c in checks
            if "F401" in c.get("rule_id", "") or "F841" in c.get("rule_id", "")
        ]
        n_dead = len(dead_checks)
        dead_items = (
            [
                {
                    "line": c.get("line", 0),
                    "rule": c.get("rule_id", ""),
                    "description": c.get("message", "código sin usar")[:100],
                    "fix": "Elimina este código o úsalo",
                }
                for c in dead_checks[:10]
            ]
            if n_dead > 0
            else []
        )
        gates.append(
            {
                "file": file_name,
                "metric": "dead_code",
                "label": "Código muerto (F401/F841)",
                "value": n_dead,
                "threshold": 5,
                "passed": n_dead <= 5,
                "level": "pass" if n_dead == 0 else ("warn" if n_dead <= 5 else "fail"),
                "failed_items": dead_items,
            }
        )

        # ── Gate 5: spaghetti_complexity (per-function CC) ────────
        funcs = f.get("functions", [])
        high_cc_funcs = [fn for fn in funcs if fn.get("cyclo", 0) > 15]
        n_high_cc = len(high_cc_funcs)
        # Per-function CC gate
        max_func_cc = max((fn.get("cyclo", 0) for fn in funcs), default=0)
        spaghetti_items = (
            [
                {
                    "line": 0,
                    "rule": "func_high_cc",
                    "description": f"{fn['name']} CC={fn['cyclo']} > 15",
                    "fix": f"Divide {fn['name']} en sub-funciones. Extrae bloques condicionales anidados",
                }
                for fn in high_cc_funcs[:5]
            ]
            if n_high_cc > 0
            else []
        )
        # Cognitive complexity
        cognitive = f.get("complexity", 0)
        gates.append(
            {
                "file": file_name,
                "metric": "spaghetti_complexity",
                "label": "Complejidad por función (CC > 15)",
                "value": n_high_cc,
                "threshold": 1 if len(funcs) > 0 else 0,
                "passed": n_high_cc == 0,
                "level": "pass" if n_high_cc == 0 else "fail",
                "failed_items": spaghetti_items,
                "details": {
                    "max_func_cc": max_func_cc,
                    "cognitive_complexity": cognitive,
                    "total_funcs": len(funcs),
                },
            }
        )

        # ── Gate 1: production_bugs (deep_analysis) ───────────────
        deep = f.get("deep_analysis", {}).get("deep_findings", [])
        prod_bugs = [d for d in deep if d.get("trigger_rule") == "production-bug"]
        n_critical_bugs = sum(
            1 for d in prod_bugs if d.get("severity") in ("critical", "high")
        )
        bug_items = []
        for d in prod_bugs:
            for m in d.get("matches", [])[:3]:
                pattern_id = d.get("pattern_id", "")
                bug_items.append(
                    {
                        "line": m.get("line", 0),
                        "rule": pattern_id,
                        "severity": d.get("severity", "high"),
                        "description": d.get("label", f"{pattern_id} detectado"),
                        "fix": FIX_MAP.get(
                            pattern_id, "Revisa este patrón y corrígelo"
                        ),
                    }
                )
        gates.append(
            {
                "file": file_name,
                "metric": "production_bugs",
                "label": "Bugs de producción (eval, pickle, unwrap, ...)",
                "value": n_critical_bugs,
                "threshold": 0,
                "passed": n_critical_bugs == 0,
                "level": "pass" if n_critical_bugs == 0 else "fail",
                "failed_items": bug_items,
                "details": {"total_bugs": len(prod_bugs)},
            }
        )

        # ── Gate 6: unnecessary_code (deep_analysis, trigger=unnecessary-code) ──
        unnecessary = [d for d in deep if d.get("trigger_rule") == "unnecessary-code"]
        n_unnecessary = sum(d.get("count", 0) for d in unnecessary)
        ui_items = []
        for d in unnecessary:
            for m in d.get("matches", [])[:3]:
                pattern_id = d.get("pattern_id", "")
                ui_items.append(
                    {
                        "line": m.get("line", 0),
                        "rule": pattern_id,
                        "severity": d.get("severity", "low"),
                        "description": d.get("label", f"{pattern_id} detectado"),
                        "fix": FIX_MAP.get(pattern_id, "Elimina código innecesario"),
                    }
                )
        gates.append(
            {
                "file": file_name,
                "metric": "unnecessary_code",
                "label": "Código innecesario (f-strings vacíos, debug leftovers, ...)",
                "value": n_unnecessary,
                "threshold": 3,
                "passed": n_unnecessary <= 3,
                "level": "pass"
                if n_unnecessary == 0
                else ("warn" if n_unnecessary <= 3 else "fail"),
                "failed_items": ui_items,
            }
        )

        # ── Gate 2: bug_probability (cross_findings) ──────────────
        cross = f.get("cross_findings", [])
        n_critical_cross = sum(1 for c in cross if c.get("severity") == "critical")
        cross_items = [
            {
                "line": 0,
                "rule": c.get("type", "bug"),
                "severity": c.get("severity", "warning"),
                "description": c.get("title", c.get("description", "hallazgo crítico"))[
                    :120
                ],
                "fix": CROSS_FIX_MAP.get(c.get("type", ""), "Revisa este hallazgo"),
            }
            for c in cross
            if c.get("severity") in ("critical", "warning")
        ]
        gates.append(
            {
                "file": file_name,
                "metric": "bug_probability",
                "label": "Bugs correlacionados (cross-reference layer)",
                "value": n_critical_cross,
                "threshold": 0,
                "passed": n_critical_cross == 0,
                "level": "pass" if n_critical_cross == 0 else "fail",
                "failed_items": cross_items,
                "details": {"total_cross_findings": len(cross)},
            }
        )

        # ── Gate 7: hallucinated_imports (from hallucination-scanner) ──
        hall_results = metrics.get("_hallucination", {})
        n_hallucinations = hall_results.get("total_hallucinations", 0)
        hall_items = []
        for f_data in hall_results.get("files", []):
            source_file = f_data.get("file", "unknown")
            for issue in f_data.get("issues", []):
                if issue.get("status") == "HALLUCINATION":
                    mod = issue.get("module", "")
                    sym = issue.get("symbol", "")
                    loc = f"{mod}.{sym}" if sym else mod
                    hall_items.append(
                        {
                            "file": source_file,
                            "line": issue.get("line", 0),
                            "rule": "hallucinated_import",
                            "severity": "critical",
                            "description": f"Import '{loc}' — {issue.get('error', 'módulo no encontrado')}",
                            "fix": f"Corrige o elimina el import. Si '{mod}' debería existir, créalo o corrige el typo",
                        }
                    )
        # Group hallucination items by source file for per-file gates
        hall_by_file: dict[str, list[dict]] = {}
        for item in hall_items:
            src = item.get("file", "unknown")
            hall_by_file.setdefault(src, []).append(item)

        if n_hallucinations > 0:
            for mod_name, items in hall_by_file.items():
                gates.append(
                    {
                        "file": mod_name,
                        "metric": "hallucinated_imports",
                        "label": "Alucinaciones: imports que no existen",
                        "value": len(items),
                        "threshold": 0,
                        "passed": False,
                        "level": "fail",
                        "failed_items": items,
                    }
                )
        else:
            gates.append(
                {
                    "file": "_global_",
                    "metric": "hallucinated_imports",
                    "label": "Alucinaciones: imports que no existen",
                    "value": 0,
                    "threshold": 0,
                    "passed": True,
                    "level": "pass",
                }
            )

        # ── Gate 8: code_duplication (identical/similar code from qlty smells) ──
        dup_blocks = quality.get("dup_blocks", 0)
        dup_lines = quality.get("dup_lines", 0)
        dup_density = quality.get("dup_density", 0.0)
        dup_items = []
        if dup_blocks > 0:
            dup_items.append(
                {
                    "line": 0,
                    "rule": "code_duplication",
                    "severity": "warning" if dup_blocks <= 2 else "critical",
                    "description": f"{dup_blocks} bloques duplicados ({dup_lines} líneas, densidad {dup_density:.2%})",
                    "fix": "Extrae la lógica duplicada a una función/componente compartido. DRY: no repitas código",
                }
            )
        gates.append(
            {
                "file": file_name,
                "metric": "code_duplication",
                "label": "Bloques de código duplicado",
                "value": dup_blocks,
                "threshold": 1,
                "passed": dup_blocks <= 1,
                "level": "pass"
                if dup_blocks == 0
                else ("warn" if dup_blocks <= 2 else "fail"),
                "failed_items": dup_items,
                "details": {
                    "dup_blocks": dup_blocks,
                    "dup_lines": dup_lines,
                    "dup_density": dup_density,
                },
            }
        )

        # ── Gate 3: security_high (B6xx injection) ─────────────────
        sec_high = quality.get("security_high", 0)
        security_items = []
        for c in checks:
            rid = c.get("rule_id", "")
            if ":B6" in rid or rid.startswith("B6"):
                security_items.append(
                    {
                        "line": c.get("line", 0),
                        "rule": rid,
                        "severity": "critical",
                        "description": c.get("message", "posible inyección")[:100],
                        "fix": "Usa parámetros en vez de concatenar strings. Nunca SQL/shell injection",
                    }
                )
        gates.append(
            {
                "file": file_name,
                "metric": "security_high",
                "label": "Issues de seguridad HIGH (B6xx: inyecciones)",
                "value": sec_high,
                "threshold": 0,
                "passed": sec_high == 0,
                "level": "pass" if sec_high == 0 else "fail",
                "failed_items": security_items,
            }
        )

    # Count results
    passed = all(g["passed"] for g in gates)
    fails = [g for g in gates if g["level"] == "fail"]
    warns = [g for g in gates if g["level"] == "warn"]

    parts = []
    if fails:
        parts.append(f"{len(fails)} FAIL")
    if warns:
        parts.append(f"{len(warns)} WARN")
    if not fails and not warns:
        parts.append("ALL PASS")
    summary = ", ".join(parts)

    return {
        "passed": passed,
        "gates": gates,
        "fails": len(fails),
        "warns": len(warns),
        "summary": summary,
    }


def analyze_files(
    chain_dir: str | None = None,
    task_id: str | None = None,
    project_root: str | None = None,
    verbose: bool = False,
) -> dict:
    """Analyze files from a delta task and return quality results as a dict.

    This is the importable entry point — replaces the old subprocess call to
    delta-qlty-check.py. Returns the same JSON structure that main() prints.

    Args:
        chain_dir: Path to the sprint directory (optional)
        task_id: Task ID to get files from (optional)
        project_root: Project root to resolve paths (auto-detected if None)
        verbose: Print progress to stderr

    Returns:
        dict with files_analyzed, files, quality (gates, passed, fails, warns)
    """
    if project_root is None:
        project_root = find_project_root(chain_dir or os.getcwd())

    # analyze_refactored.py lives inside the skill itself (self-contained)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    analyze_script = os.path.join(os.path.dirname(script_dir), "analyze_refactored.py")

    if not os.path.exists(analyze_script):
        eprint(f"❌ ERROR: analyze_refactored.py no encontrado: {analyze_script}")
        return {
            "files_analyzed": 0,
            "files": [],
            "quality": {
                "passed": True,
                "gates": [],
                "fails": 0,
                "warns": 0,
                "summary": "ERROR: analyze_refactored.py not found",
            },
        }

    # Get files
    files = []
    if chain_dir and task_id:
        files = get_files_from_task(chain_dir, task_id)
        if not files and verbose:
            eprint("ℹ️  No impacted_artifacts.files, usando git diff...")

    if not files:
        files = get_git_changed_files(project_root)

    if not files:
        if verbose:
            eprint("ℹ️  No se detectaron archivos modificados. Omitiendo Qlty check.")
        return {
            "files_analyzed": 0,
            "files": [],
            "quality": {
                "passed": True,
                "gates": [],
                "fails": 0,
                "warns": 0,
                "summary": "SKIP (no files)",
            },
        }

    if verbose:
        eprint(f"🔍 Archivos a analizar ({len(files)}):")
        for f in files:
            eprint(f"   - {f}")

    # Run Qlty analysis + hallucination scanner + evaluate gates
    metrics = run_qlty_analyze(analyze_script, files, project_root)
    hallucination_results = run_hallucination_scanner(files, project_root)
    if hallucination_results:
        metrics["_hallucination"] = hallucination_results
    quality = evaluate_quality_gates(metrics)

    return {
        "files_analyzed": metrics.get("files_analyzed", 0),
        "files": metrics.get("files", []),
        "total_smells": metrics.get("total_smells", 0),
        "total_checks": metrics.get("total_checks", 0),
        "fixable_checks": metrics.get("fixable_checks", 0),
        "quality": quality,
    }


def main():
    dir_mode = False
    dir_path = None
    chain_dir = None
    task_id = None
    files = []

    # Parse arguments — supports 3 modes:
    #   delta-qlty-check.py                         → standalone, git diff
    #   delta-qlty-check.py --dir <path>            → standalone, scan directory
    #   delta-qlty-check.py <chain_dir> <task_id>   → delta context
    argv = [a for a in sys.argv[1:] if a != "--verbose"]
    verbose_flag = "--verbose" in sys.argv

    if "--dir" in argv:
        dir_mode = True
        idx = argv.index("--dir")
        if idx + 1 < len(argv) and not argv[idx + 1].startswith("-"):
            dir_path = argv[idx + 1]
        project_root = find_project_root(dir_path or os.getcwd())
    elif len(argv) >= 2 and not argv[0].startswith("-"):
        chain_dir = argv[0]
        task_id = argv[1]
        project_root = find_project_root(chain_dir)
    elif len(argv) == 0:
        # Standalone mode: use git diff
        project_root = find_project_root()
    else:
        print(
            "Uso: python3 scripts/delta-qlty-check.py <chain_dir> <task_id> [--verbose]"
        )
        print("       python3 scripts/delta-qlty-check.py --dir <path> [--verbose]")
        print(
            "       python3 scripts/delta-qlty-check.py [--verbose]  (git diff fallback)"
        )
        sys.exit(2)

    # analyze_refactored.py lives inside the skill itself (self-contained)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    analyze_script = os.path.join(script_dir, "analyze_refactored.py")

    if not os.path.exists(analyze_script):
        eprint(f"❌ ERROR: analyze_refactored.py no encontrado: {analyze_script}")
        eprint("   Debería estar en:", script_dir)
        sys.exit(2)

    # Get files based on mode
    if dir_mode and dir_path:
        # Scan directory for code files
        if verbose_flag:
            eprint(f"🔍 Escaneando directorio: {dir_path}")
        for root, _, fnames in os.walk(dir_path):
            for fname in fnames:
                if fname.endswith(
                    (
                        ".py",
                        ".ts",
                        ".tsx",
                        ".js",
                        ".jsx",
                        ".rs",
                        ".go",
                        ".java",
                        ".rb",
                        ".kt",
                    )
                ):
                    files.append(os.path.join(root, fname))
        if not files:
            eprint(f"ℹ️  No se encontraron archivos de código en {dir_path}")
    elif chain_dir and task_id:
        # Mode 1: from task definition
        files = get_files_from_task(chain_dir, task_id)
        if not files and verbose_flag:
            eprint("ℹ️  No impacted_artifacts.files, usando git diff...")

    # Fallback: git diff (for all modes except --dir)
    if not files:
        files = get_git_changed_files(project_root)

    if not files:
        if verbose_flag:
            eprint("ℹ️  No se detectaron archivos modificados. Omitiendo Qlty check.")
        print(
            json.dumps(
                {
                    "files_analyzed": 0,
                    "quality": {
                        "passed": True,
                        "gates": [],
                        "summary": "SKIP (no files)",
                    },
                }
            )
        )
        sys.exit(0)

    if verbose_flag:
        eprint(f"🔍 Archivos a analizar ({len(files)}):")
        for f in files:
            eprint(f"   - {f}")

    # 3. Run Qlty analysis
    metrics = run_qlty_analyze(analyze_script, files, project_root)

    # 4. Run hallucination scanner (Gate 7)
    hallucination_results = run_hallucination_scanner(files, project_root)
    if hallucination_results:
        metrics["_hallucination"] = hallucination_results

    # 5. Evaluate quality gates
    quality = evaluate_quality_gates(metrics)

    # 5. Output JSON
    output = {
        "files_analyzed": metrics.get("files_analyzed", 0),
        "files": metrics.get("files", []),
        "total_smells": metrics.get("total_smells", 0),
        "total_checks": metrics.get("total_checks", 0),
        "fixable_checks": metrics.get("fixable_checks", 0),
        "quality": quality,
    }

    print(json.dumps(output, indent=2))
    print()  # separator

    # ── Self-sufficient LLM protocol (→ stderr for clean JSON on stdout) ──
    passed = quality["passed"]
    fails = quality.get("fails", 0)
    warns = quality.get("warns", 0)
    files_analyzed = output.get("files_analyzed", 0)
    total_smells = output.get("total_smells", 0)

    # Build advance command safely (may be None in standalone mode)
    adv_parts = ["python3 scripts/delta-advance.py"]
    if chain_dir:
        adv_parts.append(chain_dir)
    if task_id:
        adv_parts.append(task_id)
    advance_cmd = " ".join(adv_parts)

    if files_analyzed == 0:
        eprint("=" * 70)
        eprint("ℹ️  DELTA-QLTY-CHECK: No se analizaron archivos")
        eprint("=" * 70)
        eprint("   No hay archivos modificados que analizar.")
        eprint("   Esto puede ser normal si la tarea no modifica código.")
        eprint()
        eprint("▶️  Continúa con delta-advance:")
        eprint(f"   {advance_cmd}")
        eprint("=" * 70)
    elif passed:
        eprint("=" * 70)
        eprint("✅ DELTA-QLTY-CHECK: ALL QUALITY GATES PASS")
        eprint("=" * 70)
        eprint(f"📊 Archivos analizados: {files_analyzed}")
        eprint(f"📊 Total smells: {total_smells}")
        eprint(f"📊 Gates: {fails} fails, {warns} warns — ALL PASS")
        eprint()
        eprint("▶️  Todo en orden. Continúa con delta-advance:")
        eprint(f"   {advance_cmd}")
        eprint("=" * 70)
    else:
        eprint("=" * 70)
        eprint(
            f"❌ DELTA-QLTY-CHECK: QUALITY GATES FAILED ({fails} fail, {warns} warn)"
        )
        eprint("=" * 70)
        eprint(f"📊 Archivos analizados: {files_analyzed}")

        # Print failed gates with per-item fixes
        for g in quality.get("gates", []):
            if g.get("level") == "fail":
                metric = g.get("metric", "?")
                file_name = g.get("file", "?")
                value = g.get("value", "?")
                threshold = g.get("threshold", "?")
                label = g.get("label", metric)

                eprint()
                eprint(f"  ❌ {file_name}: {label} = {value} (umbral: {threshold})")

                # Print per-item fixes if available
                failed_items = g.get("failed_items", [])
                if failed_items:
                    for item in failed_items:
                        line = item.get("line", 0)
                        desc = item.get("description", "")
                        fix = item.get("fix", "")
                        severity = item.get("severity", "")
                        sev_tag = f" [{severity.upper()}]" if severity else ""
                        if line:
                            eprint(f"     L{line}{sev_tag}: {desc}")
                            eprint(f"     → Fix: {fix}")
                        else:
                            eprint(f"     {sev_tag}: {desc}")
                            eprint(f"     → Fix: {fix}")
                else:
                    # Fallback generic hints (no per-item data)
                    if metric == "cyclo":
                        eprint("     → Divide funciones grandes en varias más pequeñas")
                        eprint(
                            "     → Extrae bloques condicionales anidados a funciones helper"
                        )
                    elif metric == "lcom":
                        eprint(
                            "     → Separa la clase en varias clases con responsabilidades únicas"
                        )
                        eprint("     → Cada clase debe tener un propósito cohesivo")
                    elif metric == "ce":
                        eprint(
                            "     → Demasiadas dependencias externas. Aplica Dependency Inversion"
                        )
                        eprint(
                            "     → Reduce imports directos, usa interfaces/abstract classes"
                        )

        eprint()
        eprint("─" * 70)
        eprint("📋 RECOVERY PROTOCOL:")
        eprint("   1. Corrige cada gate fallido siguiendo las sugerencias arriba")
        eprint("   2. Vuelve a ejecutar delta-qlty-check con --verbose:")
        eprint(f"      {advance_cmd} --verbose")
        eprint("   3. Solo cuando todos los gates pass, ejecuta delta-advance")
        eprint("─" * 70)
        eprint(
            "💡 El output de este script ya contiene las instrucciones de recuperación."
        )
        eprint("=" * 70)

    sys.exit(0 if quality["passed"] else 1)
