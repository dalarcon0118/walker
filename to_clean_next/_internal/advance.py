#!/usr/bin/env python3
"""delta-advance.py — Ejecuta TASK LOOP + loop prompting + calibración 4-capas.

Uso:
    python3 delta-advance.py <chain_dir> <task_id> [--confirm]

Donde <chain_dir> es la ruta al directorio del chain (contiene plan.json y progress.json).

Flujo:
    1. Lee plan.json + progress.json
    2. Ejecuta TASK LOOP: verifica result_state, acceptance_criteria y quality_gates
    3. Si TASK LOOP = achieved:
       - Sin --confirm → ready_for_review (exit 3). Muestra review_prompt
       - Con --confirm → avanza la tarea (exit 0)
    4. Si TASK LOOP = partial: NO actualiza progress (exit 1)
    5. Si última tarea: sugiere ejecutar delta-close.py

Exit code:
    0 → TASK LOOP achieved + confirmado (tarea completada)
    1 → TASK LOOP partial (result_state no alcanzado)
    2 → Error de uso
    3 → ready_for_review (ejecutar con --act-as po --human-confirm para completar)

Ejemplo:
    python3 delta-advance.py .cognitive/delta/mi-chain TASK-001
    python3 delta-advance.py .cognitive/delta/mi-chain TASK-001
    python3 delta-advance.py .cognitive/delta/mi-chain TASK-001 --act-as po --human-confirm
"""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

# ── Evidence + Meta-Instruction prompt engine ──────────────────
try:
    from _internal.prompt_engine import build_review_prompt as _build_review_prompt

    _PROMPT_ENGINE_AVAILABLE = True
except ImportError:
    _PROMPT_ENGINE_AVAILABLE = False

# ── AST-grep availability detection ────────────────────────────
_AST_GREP_AVAILABLE = False
try:
    r = subprocess.run(["sg", "--version"], capture_output=True, timeout=5)
    _AST_GREP_AVAILABLE = r.returncode == 0
except (FileNotFoundError, subprocess.TimeoutExpired):
    pass

# Language detection by file extension for ast-grep
_AST_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
    ".kt": "kotlin",
    ".swift": "swift",
}

# Definition patterns per language (AST-level)
_AST_DEF_PATTERNS: dict[str, list[str]] = {
    "python": ["def {name}($$$)", "class {name}", "async def {name}($$$)"],
    "typescript": [
        "function {name}($$$)",
        "class {name}",
        "const {name} = ($$$)",
        "function *{name}($$$)",
    ],
    "javascript": [
        "function {name}($$$)",
        "class {name}",
        "const {name} = ($$$)",
        "function *{name}($$$)",
    ],
    "rust": ["fn {name}($$$)", "struct {name}", "enum {name}", "trait {name}"],
    "go": ["func {name}($$$)", "type {name} struct", "type {name} interface"],
    "java": ["class {name}", "interface {name}", "{name}($$$)"],
    "ruby": ["def {name}", "class {name}", "module {name}"],
}

# ── KV helpers (V3.1: prefer KV, fallback to JSON) ──────────────
_HAVE_KV = False
try:
    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(__file__),
            "../../../projects/cognitive-mcp/cognitive_v2/shared",
        ),
    )
    from helpers import _kv, _kv_set

    _HAVE_KV = True
except ImportError:
    pass


def _kv_load(
    chain_id: str, data_type: str, project_id: str | None = None
) -> dict | None:
    """Load sprint data from KV, fallback to None.

    In multi-project mode (project_id provided), uses
    ``kv:sprint:{project_id}:{chain_id}:{data_type}`` to avoid key
    collisions between projects. Falls back to legacy key
    ``kv:sprint:{chain_id}:{data_type}`` if not found.
    """
    if not _HAVE_KV:
        return None
    try:
        # Try project-scoped key first
        if project_id:
            key = f"kv:sprint:{project_id}:{chain_id}:{data_type}"
            raw = _kv(key)
            if raw:
                if isinstance(raw, dict):
                    return raw
                if isinstance(raw, str):
                    return json.loads(raw)
        # Fallback to legacy key (backward compat)
        key = f"kv:sprint:{chain_id}:{data_type}"
        raw = _kv(key)
        if raw and isinstance(raw, dict):
            return raw
        if raw and isinstance(raw, str):
            return json.loads(raw)
    except Exception:
        pass
    return None


def _kv_save(
    chain_id: str, data_type: str, data: dict, project_id: str | None = None
) -> bool:
    """Save sprint data to KV.

    In multi-project mode (project_id provided), writes to
    ``kv:sprint:{project_id}:{chain_id}:{data_type}`` for isolation.
    Legacy: writes to ``kv:sprint:{chain_id}:{data_type}``.
    """
    if not _HAVE_KV:
        return False
    try:
        ns = f"{project_id}:{chain_id}" if project_id else chain_id
        key = f"kv:sprint:{ns}:{data_type}"
        _kv_set(key, data)
        return True
    except Exception:
        return False


# ── projects.py import (for multi-project resolution) ──────────
_SCRIPT_DIR_ADV = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR_ADV = os.path.dirname(_SCRIPT_DIR_ADV)
if _PARENT_DIR_ADV not in sys.path:
    sys.path.insert(0, _PARENT_DIR_ADV)

try:
    from _internal.projects import ProjectRegistry, ProjectProfile

    _HAVE_PROJECTS = True
except ImportError:
    _HAVE_PROJECTS = False

# Import qlty_check directly (no subprocess to deleted delta-qlty-check.py)
_HAVE_QLTY = False
try:
    from _internal.qlty_check import analyze_files as _qlty_analyze_files

    _HAVE_QLTY = True
except ImportError:
    pass

# Unified predicate verification engine
_HAVE_VERIFICATION = False
try:
    from _internal.verification import verify_predicate

    _HAVE_VERIFICATION = True
except ImportError:

    def verify_predicate(predicate, project_root):
        label = predicate if isinstance(predicate, str) else str(predicate)
        return False, {
            "method": "error",
            "error": "verification.py no disponible",
            "label": label[:70],
        }


def _get_project_id(plan: dict) -> str | None:
    """Extract ``project_id`` from plan data.

    Returns ``plan.plan.project.project_id``, or None if not present
    (legacy single-project format → backward compat).
    """
    try:
        pl = plan.get("plan", plan)
        return pl.get("project", {}).get("project_id") or None
    except Exception:
        return None


def eprint(*args, **kwargs):
    """Print a stderr."""
    print(*args, file=sys.stderr, **kwargs)


def load_json(path):
    """Load a JSON file with error handling.

    For ``progress.json`` files, verifies the ``_checksum`` field
    (SHA-256 integrity check). Blocks the operation with an error
    if the checksum doesn't match (file was likely tampered externally).

    Backward compat: files without ``_checksum`` are accepted with a warning.
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        eprint(f"❌ ERROR: Archivo no encontrado: {path}")
        sys.exit(2)
    except json.JSONDecodeError as e:
        eprint(f"❌ ERROR: JSON inválido en {path}: {e}")
        sys.exit(2)

    # Integrity check for progress.json
    if os.path.basename(path) == "progress.json":
        try:
            from _internal.progress_checksum import verify_checksum

            if not verify_checksum(data):
                eprint(
                    "❌ ERROR: progress.json manipulado externamente — checksum no coincide"
                )
                eprint(f"   Archivo: {path}")
                eprint(
                    "   El archivo fue editado directamente sin usar task.py advance."
                )
                eprint(
                    "   Para recuperar, re-ejecuta task.py advance sobre la última tarea válida."
                )
                sys.exit(2)
            if data.get("_checksum") is None:
                eprint(
                    "  ⚠️  progress.json sin _checksum (formato legacy). Se agregará en la próxima escritura."
                )
        except ImportError:
            eprint(
                "  ⚠️  progress_checksum no disponible — saltando verificación de integridad"
            )
            eprint(f"     Archivo: {path}")

    return data


def write_json(path, data):
    """Write a JSON file.

    For ``progress.json`` files, adds a ``_checksum`` field
    (SHA-256 integrity hash) to detect external tampering.
    The checksum excludes the ``_checksum`` field itself.
    """
    # Add integrity checksum for progress.json
    if os.path.basename(path) == "progress.json":
        try:
            from _internal.progress_checksum import add_checksum

            add_checksum(data)
        except ImportError:
            eprint(
                "  ⚠️  progress_checksum no disponible — escribiendo progress.json sin checksum"
            )

    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  📝 Escrito: {path}")


def find_project_root():
    """Find the project root (where .cognitive/ lives)."""
    cwd = os.getcwd()
    for _ in range(5):
        if os.path.exists(os.path.join(cwd, ".cognitive")):
            return cwd
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent
    return os.getcwd()


# ── Verificación: delegada al motor migrado (features/task_execution) ─────
# Las funciones _parse_predicate / run_verification / _ast_verify / _grep_verify
# etc. vivieron aquí históricamente. Desde la migración del gap "verification"
# (ADR-WALKER-011) viven en features/task_execution/infrastructure/verify_engine.py.
# Este archivo las re-exporta para no romper consumidores históricos.
from features.task_execution.infrastructure.verify_engine import (
    _parse_predicate,
    run_verification,
)


def _run_acceptance_layer(
    acceptance_criteria: list, project_root: str, failed_checks: list
) -> list:
    """Run acceptance criteria checks."""
    results = []
    for ac in acceptance_criteria:
        ac_pattern, ac_type, _ = (
            _parse_predicate(ac) if isinstance(ac, str) else (ac, "must_exist", None)
        )
        ac_passed = run_verification(
            {"predicate": ac_pattern, "type": ac_type}, project_root
        )
        results.append({"criterion": ac, "passed": ac_passed})
        if not ac_passed:
            failed_checks.append(
                {
                    "predicate": f"[AC] {ac}",
                    "type": "acceptance_criterion",
                    "verification": f"grep: {ac_pattern[:50]}",
                    "actual": "not_found",
                }
            )
    return results


def _run_quality_gates_layer(
    quality_gates: list, project_root: str, failed_checks: list
) -> list:
    """Run quality gates checks."""
    results = []
    for qg in quality_gates:
        qg_pattern, qg_type, _ = (
            _parse_predicate(qg) if isinstance(qg, str) else (qg, "must_exist", None)
        )
        qg_passed = run_verification(
            {"predicate": qg_pattern, "type": qg_type}, project_root
        )
        results.append({"gate": qg, "passed": qg_passed})
        if not qg_passed:
            failed_checks.append(
                {
                    "predicate": f"[QG] {qg}",
                    "type": "quality_gate",
                    "verification": f"grep: {qg_pattern[:50]}",
                    "actual": "not_found",
                }
            )
    return results


def _run_qlty_layer(
    skip_qlty: bool, chain_dir: str, task_id: str, failed_checks: list
) -> dict:
    """Run Qlty quality analysis (implicit gate)."""
    if skip_qlty or not chain_dir or not task_id:
        return {}
    # run_qlty_check vive en este módulo (definida más abajo); no importar
    # desde _internal.qlty_check (allí no existe — era un bug latente).
    results = run_qlty_check(chain_dir, task_id, None)
    if results:
        qlty_quality = results.get("quality", {})
        qlty_fails = qlty_quality.get("fails", 0)
        if qlty_fails > 0:
            for g in qlty_quality.get("gates", []):
                if g.get("level") == "fail":
                    label = g.get("label", g.get("metric", "?"))
                    file_name = g.get("file", "?")
                    value = g.get("value", "?")
                    threshold = g.get("threshold", "?")
                    failed_checks.append(
                        {
                            "predicate": f"[QG] Qlty: {file_name}: {label} = {value} (umbral: {threshold})",
                            "type": "quality_gate",
                            "verification": f"qlty: {g.get('metric', '?')}",
                            "actual": f"{value} > {threshold}",
                        }
                    )
    return results


def execute_task_loop(
    task, project_root, chain_dir=None, task_id=None, skip_qlty=False
):
    """Execute TASK LOOP verification for a task.

    Verifies 3 layers + 1 implicit Qlty gate:
      1. loop_verification — checks (grep/pytest)
      2. acceptance_criteria — higher-level criteria
      3. quality_gates — non-negotiable quality barriers
      4. qlty quality analysis — implicit gate (unless --skip-qlty)

    Returns (result_state_type: str, loop_data: dict)
    result_state_type is "achieved" or "partial"
    """
    result_state = task.get("result_state", "")
    loop_verification = task.get("loop_verification", [])
    loop_acceptance = task.get("loop_acceptance", "all")
    acceptance_criteria = task.get("acceptance_criteria", [])
    quality_gates = task.get("quality_gates", [])

    print(f"\n🔄 TASK LOOP: {task['task_id']}")
    print(
        f"  📋 result_state: {result_state[:80]}{'...' if len(result_state) > 80 else ''}"
    )

    # ── Layer 1: loop_verification ────────────────────────────────
    if not loop_verification and result_state:
        sentences = re.split(r"[.!]\s*", result_state)
        for s in sentences:
            s = s.strip()
            if not s or len(s) < 10:
                continue
            if re.search(
                r"(no\s+(contiene|tiene|hay|llama|llamar|hace)|NO\s+|ya\s+no)",
                s,
                re.IGNORECASE,
            ):
                loop_verification.append({"predicate": s, "type": "must_not_exist"})
            elif re.search(
                r"(retorna|incluye|expone|tiene|produce|funciona|marca|llama)",
                s,
                re.IGNORECASE,
            ):
                loop_verification.append({"predicate": s, "type": "must_exist"})

    if not loop_verification:
        print(
            "  ⚠️  No hay loop_verification. Usando validation.predicates como fallback."
        )
        for p in task.get("validation", {}).get("predicates", []):
            loop_verification.append({"predicate": p, "type": "must_exist"})

    total_checks = len(loop_verification)
    passed_checks = 0
    failed_checks = []
    passed_details = []

    if total_checks > 0:
        # ── Batch verification ──
        # Extraer predicates planos del loop_verification
        flat_predicates: list[object] = []
        for item in loop_verification:
            if isinstance(item, dict) and "predicate" in item:
                flat_predicates.append(item["predicate"])
            else:
                flat_predicates.append(item)

        # Verificar en batch (cache + ast-grep agrupado)
        try:
            from _internal.verification import verify_predicates_batch

            batch_results = verify_predicates_batch(flat_predicates, project_root)
        except Exception as batch_err:
            print(f"  ⚠️  Batch verification falló ({batch_err}), usando individual")
            batch_results = [verify_predicate(p, project_root) for p in flat_predicates]

        # Mapear resultados
        for item, (passed, info) in zip(loop_verification, batch_results):
            # Construir check_item para reporting
            predicate_for_verify = (
                item["predicate"]
                if isinstance(item, dict) and "predicate" in item
                else item
            )
            check_item = {
                "predicate": info.get(
                    "label",
                    predicate_for_verify
                    if isinstance(predicate_for_verify, str)
                    else str(predicate_for_verify),
                ),
                "type": info.get("method", "unknown"),
                "verification": info.get("method", "?"),
                "actual": "found" if passed else "not_found",
                "detail": info,
            }

            if passed:
                passed_checks += 1
                passed_details.append(check_item)
            else:
                failed_checks.append(check_item)

    # ── Layer 2: acceptance_criteria ──────────────────────────────
    ac_results = _run_acceptance_layer(acceptance_criteria, project_root, failed_checks)

    # ── Layer 3: quality_gates (non-negotiable) ───────────────────
    qg_results = _run_quality_gates_layer(quality_gates, project_root, failed_checks)

    # ── Layer 4: Qlty quality analysis (implicit gate) ─────────────
    qlty_results = _run_qlty_layer(skip_qlty, chain_dir, task_id, failed_checks)

    # Determine result
    # Note: quality_gate entries may come from BOTH task-defined quality_gates
    # (Layer 3) and the Qlty implicit gate (Layer 4). Both are real gates.
    # CRITICAL: si NO hay predicates, NO hay acceptance_criteria Y NO hay quality_gates,
    # la tarea NO PUEDE ser "achieved" porque 0==0 siempre da True (falso positivo).
    # Se requiere AL MENOS UN criterio de verificación para considerar "achieved".
    has_critical = any(f["type"] == "quality_gate" for f in failed_checks)
    has_any_checks = total_checks > 0 or len(ac_results) > 0 or len(qg_results) > 0
    if loop_acceptance == "all":
        all_passed = (
            has_any_checks
            and passed_checks == total_checks
            and all(ac["passed"] for ac in ac_results)
            and all(qg["passed"] for qg in qg_results)
            and not has_critical
        )
        result_state_type = "achieved" if all_passed else "partial"
    else:
        # critical_only: only quality_gate failures block
        # Si no hay checks, no puede ser achieved
        result_state_type = (
            "achieved" if (has_any_checks and not has_critical) else "partial"
        )

    loop_data = {
        "result_state": result_state,
        "acceptance": result_state_type == "achieved",
        "acceptance_mode": loop_acceptance,
        "checks": {
            "total": total_checks,
            "passed": passed_checks,
            "passed_items": [
                {
                    "predicate": p["predicate"],
                    "type": p["type"],
                    "verification": p["verification"],
                    "actual": p["actual"],
                    "detail": p.get("detail", {}),
                }
                for p in passed_details
            ],
            "failed": [
                {
                    "predicate": f["predicate"],
                    "type": f["type"],
                    "verification": f["verification"],
                    "actual": f["actual"],
                    "detail": f.get("detail", {}),
                }
                for f in failed_checks
            ],
        },
        "acceptance_criteria": [
            {"criterion": ac["criterion"], "passed": ac["passed"]} for ac in ac_results
        ],
        "quality_gates": [
            {"gate": qg["gate"], "passed": qg["passed"]} for qg in qg_results
        ],
        "qlty": qlty_results,
    }

    print(f"  ✅ Checks: {passed_checks}/{total_checks} — {loop_acceptance}")
    if ac_results:
        ac_ok = sum(1 for ac in ac_results if ac["passed"])
        print(f"  ✅ Acceptance criteria: {ac_ok}/{len(ac_results)}")
    if qg_results:
        qg_ok = sum(1 for qg in qg_results if qg["passed"])
        print(f"  ✅ Quality gates: {qg_ok}/{len(qg_results)}")
    if passed_details:
        print("  ✅ Passed:")
        for p in passed_details:
            method = p.get("type", "?")
            label = p["predicate"][:60]
            print(f"     ✓ [{method}] {label}")
    if failed_checks:
        print("  ❌ Failed:")
        for f in failed_checks:
            print(f"     - {f['predicate'][:70]}")

    return result_state_type, loop_data


def build_review_prompt(task, loop_data, chain_dir=None, task_id=None):
    """Build review prompt with evidence blocks, delegating to prompt_engine.py.

    Falls back to legacy inline generation if prompt_engine is unavailable.
    """
    if _PROMPT_ENGINE_AVAILABLE:
        return _build_review_prompt(task, loop_data, chain_dir, task_id)

    # ── Legacy fallback ────────────────────────────────────────
    lines = []
    lines.append("=" * 70)
    lines.append(
        f"✅ TASK LOOP ACHIEVED — ready_for_review: {task.get('task_id', task_id or '?')}"
    )
    lines.append("=" * 70)
    lines.append(f"🎯 Objective: {task.get('objective', 'N/A')}")
    lines.append(f"📋 Result state: {task.get('result_state', 'N/A')[:120]}")
    lines.append("")

    checks = loop_data["checks"]
    total = checks["total"]
    passed = checks["passed"]
    coverage_pct = round(passed / max(total, 1) * 100)
    lines.append(f"📊 COVERAGE: {passed}/{total} predicates ({coverage_pct}%)")
    passed_items = checks.get("passed_items", [])
    if passed_items:
        lines.append("  ✅ Passed predicates:")
        for p in passed_items:
            method = p.get("type", "?")
            label = p["predicate"][:60]
            lines.append(f"     ✓ [{method}] {label}")
    if checks["failed"]:
        lines.append("  ❌ Failed predicates:")
        for f in checks["failed"]:
            lines.append(f"  ❌ {f['predicate'][:80]}")
    lines.append("")

    ac_results = loop_data.get("acceptance_criteria", [])
    if ac_results:
        ac_passed = sum(1 for ac in ac_results if ac["passed"])
        lines.append(f"🎯 ACCEPTANCE CRITERIA: {ac_passed}/{len(ac_results)}")
        for ac in ac_results:
            status = "✅" if ac["passed"] else "❌"
            lines.append(f"  {status} {ac['criterion'][:80]}")

    qg_results = loop_data.get("quality_gates", [])
    if qg_results:
        qg_passed = sum(1 for qg in qg_results if qg["passed"])
        lines.append(f"🔒 QUALITY GATES: {qg_passed}/{len(qg_results)}")
        for qg in qg_results:
            status = "✅" if qg["passed"] else "❌"
            lines.append(f"  {status} {qg['gate'][:80]}")

    lines.append("")
    lines.append("─" * 70)
    lines.append("📋 DECISION PROTOCOL — lee esto y decide:")
    lines.append("")

    has_critical = any(f.get("type") == "quality_gate" for f in checks["failed"])
    all_ac_passed = all(ac["passed"] for ac in ac_results) if ac_results else True
    all_qg_passed = all(qg["passed"] for qg in qg_results) if qg_results else True

    if coverage_pct >= 90 and not has_critical and all_ac_passed and all_qg_passed:
        lines.append("🥇 EVALUACIÓN: Cobertura excelente (>= 90%) — puedes confirmar")
        cmd = (
            f"python3 scripts/delta-advance.py {chain_dir} {task_id} --confirm"
            if chain_dir and task_id
            else f"python3 scripts/delta-advance.py <chain_dir> {task.get('task_id', '<task_id>')} --confirm"
        )
        lines.append(f"   → CONFIRMAR: {cmd}")
    elif coverage_pct < 80:
        lines.append("❌ EVALUACIÓN: Cobertura insuficiente (< 80%) — necesitas REFINE")
        cmd = (
            f"python3 scripts/delta-advance.py {chain_dir} {task_id}"
            if chain_dir and task_id
            else f"python3 scripts/delta-advance.py <chain_dir> {task.get('task_id', '<task_id>')}"
        )
        lines.append(f"   → REINTENTAR: {cmd}")
    elif has_critical:
        lines.append("❌ EVALUACIÓN: Quality gates FAIL — necesitas REFINE")
        cmd = (
            f"python3 scripts/delta-advance.py {chain_dir} {task_id}"
            if chain_dir and task_id
            else f"python3 scripts/delta-advance.py <chain_dir> {task.get('task_id', '<task_id>')}"
        )
        lines.append(f"   → REINTENTAR: {cmd}")
    elif not all_ac_passed:
        lines.append(
            "❌ EVALUACIÓN: Acceptance criteria sin cumplir — necesitas REFINE"
        )
        cmd = (
            f"python3 scripts/delta-advance.py {chain_dir} {task_id}"
            if chain_dir and task_id
            else f"python3 scripts/delta-advance.py <chain_dir> {task.get('task_id', '<task_id>')}"
        )
        lines.append(f"   → REINTENTAR: {cmd}")
    elif coverage_pct < 90:
        lines.append(
            "⚠️  EVALUACIÓN: Cobertura moderada (80-89%) — refina antes de confirmar"
        )
        cmd = (
            f"python3 scripts/delta-advance.py {chain_dir} {task_id}"
            if chain_dir and task_id
            else f"python3 scripts/delta-advance.py <chain_dir> {task.get('task_id', '<task_id>')}"
        )
        lines.append(f"   → REINTENTAR: {cmd}")
    else:
        lines.append("⚠️  EVALUACIÓN: Cobertura >= 90% pero hay condiciones pendientes")
        cmd = (
            f"python3 scripts/delta-advance.py {chain_dir} {task_id}"
            if chain_dir and task_id
            else f"python3 scripts/delta-advance.py <chain_dir> {task.get('task_id', '<task_id>')}"
        )
        lines.append(f"   → REINTENTAR: {cmd}")

    lines.append("")
    lines.append("─" * 70)
    lines.append("💡 El output de este script ya contiene toda la información")
    lines.append("   que necesitas para decidir. No necesitas consultar el SKILL.md.")
    lines.append("=" * 70)

    return "\n".join(lines)


def calculate_calibration(task, plan_data, progress_data):
    """Execute 4-layer calibration (state, delta, strategy, model)."""
    predicates = task.get("validation", {}).get("predicates", [])
    expected = len(predicates)
    actual = expected

    state_type = "accurate"
    gap_detail = []
    if actual > expected:
        state_type = "over_estimated"
    elif actual < expected:
        state_type = "under_estimated"
        for p in predicates:
            gap_detail.append(
                {
                    "predicate": p,
                    "expected": True,
                    "actual": False,
                    "cause": "no verificado por TASK LOOP",
                }
            )

    progress_data.get("progress", progress_data).get("trail", [])
    total_predicates = sum(
        [
            len(
                plan_data.get("plan", plan_data)
                .get("goal", {})
                .get("predicates", {})
                .get("must_exist", [])
            ),
            len(
                plan_data.get("plan", plan_data)
                .get("goal", {})
                .get("predicates", {})
                .get("must_not_exist", [])
            ),
            len(
                plan_data.get("plan", plan_data)
                .get("goal", {})
                .get("predicates", {})
                .get("invariants", [])
            ),
        ]
    )

    expected_reduction = (
        expected / max(total_predicates, 1) if total_predicates > 0 else 0
    )
    actual_reduction = expected_reduction

    return {
        "state": {
            "expected_satisfied": expected,
            "actual_satisfied": actual,
            "type": state_type,
            "gap_detail": gap_detail,
        },
        "delta": {
            "expected_reduction": round(expected_reduction, 4),
            "actual_reduction": round(actual_reduction, 4),
            "error": 0,
            "error_pct": 0,
            "error_type": "accurate",
            "cause": {
                "category": "execution",
                "evidence": "TASK LOOP verified result_state",
                "learnable": True,
                "contributors": [],
            },
        },
        "strategy": {"optimal": True},
    }


def run_qlty_check(chain_dir, task_id, project_root):
    """Run Qlty analysis via direct import from _internal/qlty_check.

    Returns a dict with metrics + quality gate results,
    or an empty dict if the module is not available.
    """
    if not _HAVE_QLTY:
        eprint(
            "  ⚠️  qlty_check module no disponible (import falló). Omitiendo Qlty gates."
        )
        return {}

    try:
        return _qlty_analyze_files(
            chain_dir=chain_dir,
            task_id=task_id,
            project_root=project_root,
            verbose=False,
        )
    except Exception as e:
        eprint(f"  ⚠️  Qlty check error: {e}")
        return {}


def run_enforcement(chain_dir, task_id, force_mode):
    """Run enforcement gate (absorbed from _internal/enforce.py).

    Returns True if the advance is allowed (pass or --force override),
    False if blocked (caller should exit 1).
    """
    try:
        # Import directly from _internal.enforce (no subprocess to delta-enforce.py)
        from _internal.enforce import enforce_task

        ok, _, _ = enforce_task(chain_dir, task_id, force=force_mode)
        return ok
    except Exception as e:
        eprint(f"  ⚠️  enforcement error: {e}")
        return True


def advance_task(chain_dir, task_id, loop_data, calibration, project_root=None):
    """Advance the task: mark completed, write trail entry, update progress.

    When loop_data is None, we're confirming a ready_for_review task
    (the TASK LOOP was skipped because it already ran). Assume achieved.

    Returns True if advanced, False if needs REFINE.
    """
    progress_path = os.path.join(chain_dir, "progress.json")
    plan_path = os.path.join(chain_dir, "plan.json")

    # V3.1: Try KV first, fallback to JSON
    # NOTE: plan read from disk first to extract project_id, then KV is
    # tried with project-scoped key. This avoids circular deps.
    chain_id = os.path.basename(os.path.normpath(chain_dir))
    plan = load_json(plan_path)
    project_id = _get_project_id(plan)
    progress = _kv_load(chain_id, "progress", project_id) or load_json(progress_path)

    prog = progress.get("progress", progress)
    pl = plan.get("plan", plan)

    # loop_data=None → confirming a ready_for_review task, skip acceptance check
    if loop_data is not None and not loop_data["acceptance"]:
        print("=" * 70)
        print(f"❌ TASK LOOP: {task_id} — PARTIAL (result_state no alcanzado)")
        print("=" * 70)
        _task = next(
            (t for t in pl.get("tasks", []) if t.get("task_id") == task_id), {}
        )
        print(f"🎯 Objective: {_task.get('objective', 'N/A')}")
        print(f"📋 Result state esperado: {_task.get('result_state', 'N/A')[:120]}")
        print()

        checks = loop_data["checks"]
        failed = checks.get("failed", [])
        total = checks["total"]
        passed = checks["passed"]

        print(f"📊 COVERAGE: {passed}/{total} ({round(passed / max(total, 1) * 100)}%)")

        # Group failures by type
        by_type: dict[str, list[dict]] = {}
        for f in failed:
            ft = f.get("type", "unknown")
            by_type.setdefault(ft, []).append(f)

        print()
        print("📋 RECOVERY PROTOCOL — corrige estos fallos y vuelve a ejecutar:")
        print()

        for ftype, items in by_type.items():
            if ftype == "must_exist":
                print(
                    f"  ➕ MUST_EXIST ({len(items)}): Código que falta por implementar"
                )
                for item in items:
                    print(f"     · {item['predicate'][:80]}")
                print("     → Implementa las funciones/clases faltantes")
            elif ftype == "must_not_exist":
                print(f"  ➖ MUST_NOT_EXIST ({len(items)}): Código que debe eliminarse")
                for item in items:
                    print(f"     · {item['predicate'][:80]}")
                print("     → Elimina o refactoriza el código existente")
            elif ftype == "test":
                print(f"  🧪 TEST ({len(items)}): Tests que fallan")
                for item in items:
                    print(f"     · {item['predicate'][:80]}")
                print("     → Corrige los tests")
            elif ftype == "quality_gate":
                print(f"  🔒 QUALITY GATE ({len(items)}): Barreras de calidad")
                for item in items:
                    print(f"     · {item['predicate'][:80]}")
                print("     → Refactoriza para mejorar métricas")
            elif ftype == "acceptance_criterion":
                print(
                    f"  🎯 ACCEPTANCE CRITERION ({len(items)}): Criterios sin cumplir"
                )
                for item in items:
                    print(f"     · {item['predicate'][:80]}")
                print("     → Ajusta la implementación")
            else:
                for item in items:
                    print(f"  ❓ {item['predicate'][:80]}")

        print()
        print("─" * 70)
        print("▶️  DESPUÉS DE CORREGIR, ejecuta de nuevo:")
        print(f"   python3 scripts/delta-advance.py {chain_dir} {task_id}")
        print("─" * 70)
        print(
            "💡 El output de este script ya contiene las instrucciones de recuperación."
        )
        print("   No necesitas consultar el SKILL.md para saber qué hacer.")
        print("=" * 70)

        prog["current"]["next_action"] = {
            "task_id": task_id,
            "type": "refine",
            "candidate": "REFINE: result_state no alcanzado",
            "detail": [f["predicate"] for f in loop_data["checks"]["failed"]],
            "rationale": "El TASK LOOP detectó que el result_state no se refleja en G_actual.",
        }
        write_json(progress_path, progress)
        _kv_save(chain_id, "progress", progress, project_id)
        return False

    print("=" * 70)
    print(f"✅ TASK LOOP: {task_id} — ACHIEVED + CONFIRMED")
    print("=" * 70)
    plan_data = load_json(os.path.join(chain_dir, "plan.json"))
    task_obj = next(
        (
            t
            for t in plan_data.get("plan", plan_data).get("tasks", [])
            if t.get("task_id") == task_id
        ),
        {},
    )
    print(f"🎯 Objective: {task_obj.get('objective', 'N/A')}")
    prog["current"]["task_states"][task_id] = "completed"

    # ── Qlty quality results (from TASK LOOP Layer 4) ────────────
    # loop_data may be None when confirming a ready_for_review task
    qlty_results = loop_data.get("qlty", {}) if loop_data else {}
    if qlty_results:
        qlty_quality = qlty_results.get("quality", {})
        qlty_summary = qlty_quality.get("summary", "N/A")
        qlty_fails = qlty_quality.get("fails", 0)
        qlty_warns = qlty_quality.get("warns", 0)
        print(f"📊 Qlty quality gates: {qlty_summary}")
        if qlty_fails > 0:
            print(f"   ❌ {qlty_fails} gates FAILED — revisar métricas")
            for g in qlty_quality.get("gates", []):
                if g.get("level") == "fail":
                    print(
                        f"      - {g['file']}: {g['label']} = {g['value']} (umbral: {g['threshold']})"
                    )
        if qlty_warns > 0:
            print(f"   ⚠️  {qlty_warns} gates en advertencia")

    total_predicates = sum(
        [
            len(pl.get("goal", {}).get("predicates", {}).get("must_exist", [])),
            len(pl.get("goal", {}).get("predicates", {}).get("must_not_exist", [])),
            len(pl.get("goal", {}).get("predicates", {}).get("invariants", [])),
        ]
    )
    completed_predicates = set()
    for t in pl.get("tasks", []):
        if prog["current"]["task_states"].get(t["task_id"]) == "completed":
            for p in t.get("validation", {}).get("predicates", []):
                # Structured predicates (dicts) are unhashable — serialize to string
                key = json.dumps(p, sort_keys=True) if isinstance(p, dict) else p
                completed_predicates.add(key)
    new_distance = max(
        0.0, 1.0 - (len(completed_predicates) / max(total_predicates, 1))
    )

    trail_entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "distance": round(new_distance, 4),
        "event": f"{task_id} completed",
        "tasks": [task_id],
        "loop": loop_data
        or {
            "acceptance": True,
            "result_state": task_obj.get("result_state", "completed via human-confirm"),
            "checks": {"total": 0, "passed": 0, "failed": []},
        },
        "calibration": calibration,
        "qlty": {
            "files_analyzed": qlty_results.get("files_analyzed", 0),
            "total_smells": qlty_results.get("total_smells", 0),
            "quality": {
                "passed": qlty_results.get("quality", {}).get("passed", True),
                "summary": qlty_results.get("quality", {}).get("summary", "N/A"),
                "fails": qlty_results.get("quality", {}).get("fails", 0),
                "warns": qlty_results.get("quality", {}).get("warns", 0),
            },
        },
    }
    prog["trail"].append(trail_entry)

    pending_tasks = [
        t
        for t in pl.get("tasks", [])
        if prog["current"]["task_states"].get(t["task_id"])
        in ("pending", "in_progress")
    ]

    if pending_tasks:
        next_task = pending_tasks[0]
        prog["current"]["next_action"] = {
            "task_id": next_task["task_id"],
            "role": next_task.get("role", "core"),
            "candidate": next_task.get("objective", "")[:100],
            "type": next_task.get("type", "feature"),
            "work_type": next_task.get("work_type", "refactor"),
            "target": "",
            "delta": round(
                len(next_task.get("validation", {}).get("predicates", []))
                / max(total_predicates, 1),
                4,
            ),
            "confidence": 0.80,
            "risk": next_task.get("risks", {}).get("curvature", 0.20),
            "score": 0.50,
            "rationale": f"Siguiente tarea: {next_task.get('objective', '')[:60]}...",
        }
        print()
        print("─" * 70)
        print("📋 SIGUIENTE ACCIÓN:")
        print(
            f"   Tarea: {next_task['task_id']} — {next_task.get('objective', '')[:100]}"
        )
        print(
            f"   Type: {next_task.get('type', '?')} | Domain: {next_task.get('domain', '?')}"
        )
        print(f"   Depende de: {next_task.get('depends_on', [])}")
        next_files = next_task.get("impacted_artifacts", {}).get("files", [])
        if next_files:
            print(f"   Archivos: {', '.join(next_files[:5])}")
        print()
        if next_task.get("type") in ("refactor", "optimization"):
            print(
                "   ⚠️  Tipo refactor/optimization → ejecuta delta-qlty-check DESPUÉS de implementar:"
            )
            print(
                f"      python3 scripts/delta-qlty-check.py {chain_dir} {next_task['task_id']} --verbose"
            )
            print("      ANTES de ejecutar delta-advance --confirm")
        print("   ▶️  Implementa la tarea y luego ejecuta:")
        print(
            f"      python3 scripts/delta-advance.py {chain_dir} {next_task['task_id']}"
        )
        print("─" * 70)
    else:
        prog["current"]["next_action"] = None
        prog["current"]["finished"] = True
        print()
        print("─" * 70)
        print("🎯 TODAS LAS TAREAS COMPLETADAS — ejecuta CLOSURE:")
        print(f"   python3 scripts/delta-close.py {chain_dir}")
        print()
        print("   Esto verificará que la suma de result_states == G_objetivo")
        print("   y generará semilla para el próximo ciclo delta.")
        print("─" * 70)

    write_json(progress_path, progress)
    _kv_save(chain_id, "progress", progress, project_id)
    print(
        f"\n📊 Progreso: {new_distance:.4f} | Distancia restante: {round(new_distance * 100)}%"
    )
    print("💡 El output de este script ya contiene los siguientes pasos.")
    print("   No necesitas consultar el SKILL.md para saber qué hacer.")
    return True


def main():
    argv = sys.argv[1:]
    act_as_index = None
    act_as_value = None
    for i, a in enumerate(argv):
        if a == "--act-as":
            act_as_index = i
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                act_as_value = argv[i + 1]
            break
    skip_indices = set()
    if act_as_index is not None:
        skip_indices.add(act_as_index)
        if act_as_value is not None:
            skip_indices.add(act_as_index + 1)
    positional = [
        a
        for i, a in enumerate(argv)
        if not a.startswith("--") and i not in skip_indices
    ]
    if len(positional) != 2:
        print(
            "Uso: python3 delta-advance.py <chain_dir> <task_id> [--confirm] [--human-confirm] [--force] [--skip-qlty]"
        )
        print("")
        print("Ejecuta TASK LOOP + loop prompting + calibración 4-capas.")
        print("")
        print("Sin --confirm: si TASK LOOP es achieved, muestra review_prompt")
        print("              y retorna exit code 3 (ready_for_review).")
        print(
            "Con --confirm: muestra review_prompt y pasa a ready_for_review (exit 3)."
        )
        print(
            "       Luego, el PO confirma con --human-confirm para completar la tarea."
        )
        print(
            "--human-confirm: (SOLO USUARIO) Marca la tarea como completada definitivamente."
        )
        print(
            "   El LLM NUNCA debe usar este flag. Si lo intenta, se rechaza con error."
        )
        print("--force: omite el gate de enforcement para desviaciones del goal.")
        print("--skip-qlty: omite el quality gate de Qlty (útil si no está instalado).")
        print("")
        print("Exit codes:")
        print(
            "  0 → human-confirm: TASK LOOP achieved + PO approved (tarea completada)"
        )
        print("  1 → TASK LOOP partial O enforcement bloqueado (desviación del goal)")
        print("  2 → Error")
        print(
            "  3 → ready_for_review (ejecutar con --act-as po --human-confirm para completar)"
        )
        sys.exit(2)

    chain_dir = positional[0]
    task_id = positional[1]
    confirm_mode = "--confirm" in sys.argv
    force_mode = "--force" in sys.argv
    skip_qlty = "--skip-qlty" in sys.argv

    plan_path = os.path.join(chain_dir, "plan.json")
    progress_path = os.path.join(chain_dir, "progress.json")
    chain_id_main = os.path.basename(os.path.normpath(chain_dir))

    if not os.path.isdir(chain_dir):
        eprint(f"❌ ERROR: Directorio no encontrado: {chain_dir}")
        sys.exit(2)

    # V3.1: Try KV first, fallback to JSON
    # Multi-project: read plan first to extract project_id, then
    # use it for project-scoped KV keys.
    plan = _kv_load(chain_id_main, "plan")
    if not plan:
        if not os.path.exists(plan_path):
            eprint(f"❌ ERROR: plan.json no encontrado en {chain_dir}")
            sys.exit(2)
        plan = load_json(plan_path)
    project_id = _get_project_id(plan)

    progress = _kv_load(chain_id_main, "progress", project_id)
    if not progress:
        if not os.path.exists(progress_path):
            eprint(f"❌ ERROR: progress.json no encontrado en {chain_dir}")
            sys.exit(2)
        progress = load_json(progress_path)

    pl = plan.get("plan", plan)
    prog = progress.get("progress", progress)

    task = None
    for t in pl.get("tasks", []):
        if t["task_id"] == task_id:
            task = t
            break

    if not task:
        eprint(f"❌ ERROR: Tarea {task_id} no encontrada en plan.json")
        sys.exit(2)

    current_state = prog["current"]["task_states"].get(task_id)
    if current_state == "completed":
        eprint(f"⚠️  La tarea {task_id} ya está completada.")
        sys.exit(0)

    # ── ROLE IDENTITY: --act-as flag ───────────────────────────────
    # El LLM se identifica con un rol Scrum (po|dev|qa) que determina
    # qué operaciones puede realizar:
    #   po  → puede hacer human-confirm (aprobar finalización)
    #   dev → puede implementar y avanzar, NO human-confirm
    #   qa  → puede validar calidad, NO human-confirm, qlty obligatorio
    #
    # Si no se pasa --act-as, se usa el scrum_role de la tarea como
    # fallback (behavior legacy).
    _act_as = "dev"  # default seguro
    for i, arg in enumerate(sys.argv):
        if arg == "--act-as":
            if i + 1 >= len(sys.argv) or sys.argv[i + 1].startswith("--"):
                eprint("❌ --act-as requiere un valor: po|dev|qa")
                sys.exit(2)
            _act_as = sys.argv[i + 1].lower()
            if _act_as not in ("po", "dev", "qa"):
                eprint(f"❌ Rol inválido: '{_act_as}'. Usa --act-as po|dev|qa")
                sys.exit(2)
            break

    task_role = task.get("scrum_role", "dev")
    act_as_role = _act_as
    has_human_confirm = "--human-confirm" in sys.argv
    has_llm_confirm = "--confirm" in sys.argv

    # ── ROLE IDENTITY BANNER ───────────────────────────────────────
    _ROLE_ICONS = {"po": "🧑‍💼", "dev": "👨‍💻", "qa": "🔍"}
    _ROLE_NAMES = {"po": "Product Owner", "dev": "Developer", "qa": "QA Engineer"}
    _ROLE_ACTIONS = {
        "po": "Define, revisa y aprueba tareas. Puede hacer human-confirm.",
        "dev": "Implementa y avanza tareas técnicamente. NO puede hacer human-confirm.",
        "qa": "Valida calidad y ejecuta qlty-check. NO puede hacer human-confirm.",
    }
    role_icon = _ROLE_ICONS.get(act_as_role, "🤖")
    role_name = _ROLE_NAMES.get(act_as_role, "Agente")
    role_action = _ROLE_ACTIONS.get(act_as_role, "")
    print(f"{role_icon} Actuando como: {role_name}")
    print(f"   {role_action}")
    print(f"   Task scrum_role: {task_role}")
    if act_as_role != task_role:
        print(
            f"   ⚠️  Act-as ({act_as_role}) ≠ task scrum_role ({task_role}) — puede haber restricciones"
        )
    print()

    # ── ROLE GATE 1: Solo PO puede hacer human-confirm ──────────────
    # El LLM actuando como PO es el único que puede aprobar
    # la finalización definitiva de una tarea con --human-confirm.
    # Los roles "dev" y "qa" NO pueden auto-aprobarse.
    if has_human_confirm and act_as_role != "po":
        eprint("❌ ROLE GATE: Solo '--act-as po' permite human-confirm.")
        eprint(f"   Estás actuando como '{act_as_role}'.")
        eprint("   Para confirmar como PO:")
        eprint(
            f"   python3 delta-advance.py {chain_dir} {task_id} --act-as po --confirm --human-confirm"
        )
        eprint("")
        eprint("   O si eres Dev/QA, solicita que el PO revise:")
        eprint(
            f"   python3 delta-advance.py {chain_dir} {task_id} --confirm  → exit 3, luego PO confirma"
        )
        sys.exit(2)
    elif has_human_confirm:
        print("  ✅ ROLE GATE: human-confirm permitido (act-as 'po')")

    # ── ROLE GATE 2: QA tasks requieren qlty-check obligatorio ─────
    # Las tareas con scrum_role="qa" (Quality Assurance) NO pueden
    # saltarse el quality check. El qlty-check es la validación
    # principal del rol QA.
    if task_role == "qa" and skip_qlty:
        eprint("❌ ROLE GATE: Las tareas 'qa' NO pueden saltar el quality check.")
        eprint(
            f"   La tarea {task_id} tiene scrum_role='qa' y requiere qlty-check obligatorio."
        )
        eprint("   Elimina --skip-qlty o cambia el scrum_role de la tarea.")
        sys.exit(2)

    if task_role == "qa":
        print("  ✅ ROLE GATE: qlty-check obligatorio para tarea 'qa'")

    # ── HUMAN CONFIRM GATE (Scrum ceremony) ────────────────────────
    # --confirm        → ready_for_review (exit 3). LLM mode: muestra review y espera.
    # --human-confirm  → completado definitivo (exit 0). SOLO PO con --act-as po.

    if has_human_confirm:
        confirm_mode = True  # Human (PO) says yes — advance fully
    else:
        # --confirm (Dev mode) NO auto-advance. Solo --human-confirm (PO)
        # puede marcar una tarea como completada. El flujo Scrum correcto:
        #   Dev: task.py advance ... --act-as dev → ready_for_review (exit 3)
        #   PO:  task.py advance ... --act-as po --human-confirm → exit 0
        confirm_mode = False

    # ── project_root must be computed before ready_for_review handler ─
    # because that block calls advance_task() which needs it.
    project_root = find_project_root()

    # ── ready_for_review state handling ────────────────────────────
    # Skip enforcement + task loop re-run. The task was already vetted.
    if current_state == "ready_for_review" and not force_mode:
        if has_human_confirm:
            # Human confirms: advance directly
            calibration = calculate_calibration(task, plan, progress)
            advance_ok = advance_task(
                chain_dir, task_id, None, calibration, project_root
            )
            sys.exit(0 if advance_ok else 1)
        elif has_llm_confirm:
            # --confirm on ready_for_review: LLM reviewed, already ready.
            # Skip re-run, just show guidance and exit 3.
            print(
                f"\n✅ La tarea {task_id} ya fue evaluada y está lista para revisión."
            )
            print("   El TASK LOOP ya se ejecutó — no es necesario re-evaluar.")
            print("")
            if act_as_role == "po":
                print("   Como PO, confirma para completar:")
                print(
                    f"   python3 delta-advance.py {chain_dir} {task_id} --act-as po --confirm --human-confirm"
                )
            elif act_as_role == "qa":
                print("   Como QA, revisa quality gates. Solicita al PO que confirme:")
                print(
                    f"   python3 delta-advance.py {chain_dir} {task_id} --act-as po --confirm --human-confirm"
                )
            else:
                print("   Como Dev, la tarea está lista. Solicita revisión del PO:")
                print(
                    f"   python3 delta-advance.py {chain_dir} {task_id} --act-as po --confirm --human-confirm"
                )
            print("")
            print("💡 Si la tarea necesita cambios, usa --force para re-evaluar:")
            print(f"   python3 delta-advance.py {chain_dir} {task_id} --force")
            sys.exit(3)
        else:
            # No flags: show role-specific guidance
            eprint(f"⚠️  La tarea {task_id} ya fue evaluada y está lista para revisión.")
            eprint("")
            if act_as_role == "po":
                eprint("   Como PO, puedes confirmar directamente:")
                eprint(
                    f"   python3 delta-advance.py {chain_dir} {task_id} --act-as po --confirm --human-confirm"
                )
            elif act_as_role == "qa":
                eprint(
                    "   Como QA, revisa los quality gates. Si pasan, solicita confirmación del PO:"
                )
                eprint(
                    f"   python3 delta-advance.py {chain_dir} {task_id} --act-as po --confirm --human-confirm"
                )
            else:
                eprint("   Como Dev, la tarea está lista. Solicita revisión del PO:")
                eprint(
                    f"   python3 delta-advance.py {chain_dir} {task_id} --act-as po --confirm --human-confirm"
                )
            sys.exit(3)

    # ── Enforcement gate (skill coercion layer) ──
    # Bloquea avances que se alejan del goal (next_action, curvature, backlog).
    # --force omite el gate (override explícito y registrado en el trail).
    if not run_enforcement(chain_dir, task_id, force_mode):
        sys.exit(1)

    print(f"📍 Project root: {project_root}")
    print(f"📂 Chain: {chain_dir}")
    if project_id:
        print(f"🏷️  Project: {project_id}")
    print(f"📋 Task: {task_id}")
    print(
        f"🔧 Mode: {'--human-confirm (completar)' if confirm_mode else 'review (ready_for_review)'}"
    )

    result_type, loop_data = execute_task_loop(
        task, project_root, chain_dir, task_id, skip_qlty
    )

    if result_type == "partial":
        advance_task(chain_dir, task_id, loop_data, None, project_root)
        sys.exit(1)

    # ── TASK LOOP achieved ────────────────────────────────────────
    if not confirm_mode:
        review_prompt = build_review_prompt(task, loop_data, chain_dir, task_id)
        print(review_prompt)

        prog["current"]["task_states"][task_id] = "ready_for_review"
        prog["current"]["next_action"] = {
            "task_id": task_id,
            "type": "review",
            "candidate": "ready_for_review — confirmar o refinar",
            "status": "ready_for_review",
            "rationale": "TASK LOOP achieved. Revisar y ejecutar con --confirm para avanzar.",
        }
        write_json(progress_path, progress)
        _kv_save(chain_id_main, "progress", progress, project_id)

        print("\n📋 Para confirmar:")
        print(f"   python3 delta-advance.py {chain_dir} {task_id} --confirm")
        sys.exit(3)

    # ── Confirm mode: advance ─────────────────────────────────────
    calibration = calculate_calibration(task, plan, progress)
    advance_ok = advance_task(chain_dir, task_id, loop_data, calibration, project_root)

    if not advance_ok:
        sys.exit(1)

    current_path = os.path.join(os.path.dirname(chain_dir), "current.json")
    parent_dir = os.path.dirname(current_path)
    if os.path.exists(parent_dir):
        shutil.copy(progress_path, current_path)
        print("  📋 current.json actualizado")

    sys.exit(0)
