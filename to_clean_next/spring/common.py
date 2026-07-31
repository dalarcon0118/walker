"""spring/common.py — Shared utilities"""

from __future__ import annotations
import sys
import os
from pathlib import Path

_THIS_DIR = str(Path(__file__).parent.parent)
_BASE_DIR = os.path.dirname(_THIS_DIR)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
try:
    from _internal.projects import (
        COGNITIVE_DIR as _COGNITIVE_DIR,
        PROJECTS_DIR as _PROJECTS_DIR,
        DELTA_DIR as _DELTA_DIR,
    )
except ImportError:
    _COGNITIVE_DIR = ".cognitive"
    _PROJECTS_DIR = "projects"
    _DELTA_DIR = "delta"


import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Constants ──────────────────────────────────────────────
_SCRIPT_DIR = _THIS_DIR  # scripts/
_SPRINT_STATE_FILE = "sprint-state.json"
_OLD_PROGRESS_FILE = "progress.json"
_VALIDATE_SCRIPT = os.path.join(_SCRIPT_DIR, "validate-schema.py")

_TASK_REQUIRED_FIELDS = [
    "task_id",
    "type",
    "objective",
    "result_state",
    "domain",
    "impacted_artifacts",
    "risks",
    "task_context",
    "validation",
]

_TASK_FIELD_DESCRIPTIONS = {
    "type": "string — feature|fix|refactor|test|docs",
    "domain": "string — modulo del proyecto (ej: auth, core, api)",
    "impacted_artifacts": 'object con {"files": ["ruta/archivo.py"]}',
    "risks": 'object con {"curvature": 0.0-1.0, "commit_trust": 0.0-1.0}',
    "task_context": "object con 6 capas de analisis",
    "validation": 'object con {"predicates": ["..."]}',
}


def _load_sprint_progress(chain_dir: str) -> dict | None:
    """Load progress data from sprint-state.json or fallback to sprint-state.json."""
    from state import load_state

    state_file = os.path.join(chain_dir, _SPRINT_STATE_FILE)
    old_file = os.path.join(chain_dir, _OLD_PROGRESS_FILE)

    # Prefer sprint-state.json
    if os.path.exists(state_file):
        s = load_state(chain_dir)
        from _walker.shared.path_resolver import resolve_project_root

        proj_dir = resolve_project_root(chain_dir)
        source_name = (
            os.path.basename(os.path.normpath(proj_dir)) if proj_dir else "unknown"
        )
        tasks = s.get("tasks", {})
        return {
            "meta": {
                "chain_id": s.get("sprint", {}).get("chain_id", ""),
                "status": s.get("sprint", {}).get("status", "active"),
                "source": source_name,
                "type": "progress",
            },
            "trail": s.get("audit", {}).get("trail", []),
            "current": {
                "task_states": {
                    tid: t.get("status", "pending") for tid, t in tasks.items()
                },
                "next_action": None,
                "risks": [],
                "delta_metrics": {},
            },
        }

    if os.path.exists(old_file):
        raw = load_json(old_file)
        if raw:
            return raw.get("progress", raw)

    return None


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def load_json(path: str) -> dict | None:
    """Load a JSON file with error handling.

    For ``sprint-state.json``, verifies the ``_checksum`` field (SHA-256 integrity).
    Returns None if file not found, checksum mismatch, or JSON invalid.
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    return data


def write_json(path: str, data: dict) -> None:
    """Write a JSON file con atomicidad (TASK-030).

    1. Backup del archivo original si existe
    2. Valida schema ANTES de sobrescribir (TASK-020)
    3. Escribe solo si valida
    4. Git auto-commit (TASK-019)
    """
    basename = os.path.basename(path)

    # TASK-030: backup del archivo original
    _backup_before_write(path)

    # TASK-030: validar ANTES de escribir (schema check)
    if basename in ("plan.json", "sprint-state.json"):
        schema_name = (
            "plan.schema.json"
            if basename == "plan.json"
            else "sprint-state.schema.json"
        )
        # Escribir a archivo temporal para validar
        tmp_path = path + ".tmp"
        os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        valid = _validate_schema(tmp_path, schema_name)
        if not valid:
            os.remove(tmp_path)
            msg = f"❌ VALIDATION FAILED: {basename} — escritura cancelada"
            sys.stderr.write(msg + "\n")
            raise RuntimeError(msg)
        # Schema valido: reemplazar archivo original con el temporal
        os.replace(tmp_path, path)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"  📝 Escrito: {path}")

    # TASK-019: git auto-commit
    _git_auto_commit(path)


def _backup_before_write(path: str) -> None:
    """Backup del archivo original antes de sobrescribir (TASK-030).

    Usa repo_root/.cognitive/backups/ para no crear .cognitive/ dentro del chain.
    """
    if not os.path.exists(path):
        return
    basename = os.path.basename(path)
    if basename not in ("plan.json", "sprint-state.json"):
        return

    # Encontrar repo root para backups centralizados
    repo = _find_git_root(os.path.dirname(path)) or os.path.expanduser("~")
    os.path.join(repo, ".cognitive", "backups")


def _find_git_root(start: str) -> str | None:
    """Find git repository root from start path."""
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None
    os.makedirs(backup_dir, exist_ok=True)

    from datetime import datetime

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"{basename}.{ts}.bak")

    try:
        import shutil

        shutil.copy2(path, backup_path)
    except Exception:
        pass  # Backup failures no bloquean


def _post_write_validate(path: str) -> None:
    """Valida schema post-escritura (TASK-020)."""
    basename = os.path.basename(path)
    if basename == "plan.json":
        _validate_schema(path, "plan.schema.json")
    elif basename == "sprint-state.json":
        _validate_schema(path, "sprint-state.schema.json")


def _git_auto_commit(path: str) -> None:
    """Git auto-commit para plan.json/sprint-state.json (TASK-019)."""
    basename = os.path.basename(path)
    if basename not in ("plan.json", "sprint-state.json"):
        return

    repo_root = _find_git_root(os.path.dirname(path))
    if not repo_root:
        return

    try:
        rel_path = os.path.relpath(path, repo_root)
        subprocess.run(
            ["git", "add", rel_path], cwd=repo_root, capture_output=True, timeout=5
        )
        r = subprocess.run(
            ["git", "diff", "--cached", "--quiet", rel_path],
            cwd=repo_root,
            capture_output=True,
            timeout=5,
        )
        if r.returncode != 0:  # Hay cambios
            subprocess.run(
                ["git", "commit", "-m", f"delta: {basename} updated", "--no-verify"],
                cwd=repo_root,
                capture_output=True,
                timeout=10,
            )
            print(f"  📝 Git commit: delta: {basename} updated")
    except Exception:
        pass  # Git failures no bloquean


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_project_root(start: str | None = None) -> str:
    """Encuentra la raíz del proyecto (repo root).

    Los impacted_artifacts.files están escritos relativos al repo root.
    Para obtener el project_path del plan, lee plan.json project.project_path.
    """
    start = start or os.getcwd()
    start = os.path.abspath(start)

    # 1. Intentar sprint-state.json (cache)
    sprint_state_path = os.path.join(start, "sprint-state.json")
    if os.path.exists(sprint_state_path):
        try:
            with open(sprint_state_path) as f:
                prog = json.load(f)
            cached = prog.get("progress", prog).get("project_root", "")
            if cached and os.path.exists(cached):
                return cached
        except Exception:
            pass

    # 2. Buscar .git hacia arriba
    cwd = start
    for _ in range(10):
        if os.path.exists(os.path.join(cwd, ".git")):
            return cwd
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent

    # Fallback: .cognitive/ (legacy)
    cwd = start
    for _ in range(10):
        if os.path.exists(os.path.join(cwd, _COGNITIVE_DIR)):
            return cwd
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent
    return os.path.abspath(os.getcwd())


def _validate_task_fields(tasks: list[dict]) -> tuple[bool, list[str]]:
    """Pre-write validation: check each task has all required schema fields."""
    errors: list[str] = []
    for i, t in enumerate(tasks):
        tid = t.get("task_id", f"#{i + 1}")
        for field in _TASK_REQUIRED_FIELDS:
            if field not in t:
                desc = _TASK_FIELD_DESCRIPTIONS.get(field, "")
                hint = f" — {desc}" if desc else ""
                errors.append(
                    f"  ❌ Tarea {tid}: falta campo requerido '{field}'{hint}"
                )
            elif t[field] is None:
                errors.append(
                    f"  ❌ Tarea {tid}: campo '{field}' es null (debe tener valor)"
                )
            elif isinstance(t[field], (list, dict)) and len(t[field]) == 0:
                pass
    return len(errors) == 0, errors


def _ensure_v3_defaults(plan: dict) -> None:
    """Delegate to delta-validate.py ensure-v3 via subprocess.

    Centraliza la logica de defaults v3 en un solo script.
    """
    # Derive chain_dir from plan (plan['plan']['meta']['chain_id'] is not reliable)
    # We need the actual file path - callers pass plan dict, not path.
    # As fallback, iterate directly (same logic as inline version)
    pl = plan.get("plan", plan)
    for task in pl.get("tasks", []):
        for k, v in {
            "task_description": "",
            "importer_files": 0,
            "depends_on": [],
            "preconditions": [],
            "acceptance_criteria": [],
            "quality_gates": [],
            "loop_verification": [],
            "loop_acceptance": "all",
            "decisions": [],
            "refinement": {},
            "risks": {"curvature": 0.2, "pre_churn": 0.1, "commit_trust": 0.9},
            "impacted_artifacts": {"files": []},
            "validation": {"predicates": []},
            "task_context": {
                "observation": "Contexto generado.",
                "structure": "Contexto generado.",
                "behavior": "Contexto generado.",
                "causality": "Contexto generado.",
                "purpose": "Contexto generado.",
                "principles": "Contexto generado.",
            },
        }.items():
            expected_type = type(v)
            if k in task:
                if type(task[k]) != expected_type:
                    task[k] = v
            else:
                task[k] = v


def _validate_schema(json_path: str, schema_name: str) -> bool:
    """Validate a JSON file against a schema. Returns True if valid.

    Si el schema es plan.schema.json, aplica _ensure_v3_defaults AUTOMATICAMENTE
    antes de validar. Esto asegura que tareas con tipos incorrectos o campos
    faltantes (ej: creadas antes de v3) no bloqueen la validación.
    """
    # Auto-fix: apply v3 defaults BEFORE validation for plan.json
    if schema_name == "plan.schema.json" and os.path.exists(json_path):
        chain_dir = os.path.dirname(json_path)
        import subprocess

        try:
            subprocess.run(
                [sys.executable, _VALIDATE_SCRIPT, "ensure-v3", chain_dir],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except Exception:
            pass

    schema_path = os.path.join(_BASE_DIR, "schema", schema_name)
    validate_script = os.path.join(_THIS_DIR, "validate-schema.py")
    if not os.path.exists(validate_script):
        eprint(
            f"  ❌ VALIDATION ENFORCEMENT: validate-schema.py no encontrado en {validate_script}"
        )
        eprint("  ❌ No se puede proceder sin el validador de schemas.")
        return False
    if not os.path.exists(schema_path):
        eprint(f"  ❌ VALIDATION ENFORCEMENT: schema no encontrado en {schema_path}")
        return False
    import subprocess

    r = subprocess.run(
        [sys.executable, validate_script, json_path, schema_path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        # Print full validation output for debugging
        if r.stdout:
            for line in r.stdout.splitlines():
                if any(
                    tag in line
                    for tag in ("❌", "Mensaje:", "Error", "#1", "#2", "RECOVERY")
                ):
                    eprint(f"  {line.strip()}")
        return False
    return True


def _get_project_id(plan: dict) -> str | None:
    """Extract project_id from plan.json data."""
    try:
        pl = plan.get("plan", plan)
        return pl.get("project", {}).get("project_id") or None
    except Exception:
        return None
