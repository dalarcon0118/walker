"""task/common.py — Shared utilities"""

from __future__ import annotations
from __future__ import annotations
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
# ast_grep_utils: imported in research.py

_GREP_EXCLUDE_DIRS = [
    "--exclude-dir=node_modules",
    "--exclude-dir=.git",
    "--exclude-dir=__pycache__",
    "--exclude-dir=.cognitive",
    "--exclude-dir=.venv",
    "--exclude-dir=venv",
    "--exclude-dir=.tox",
]


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def load_json(path: str) -> dict | None:
    """Load a JSON file with error handling. Returns None if not found or invalid."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return data


def write_json(path: str, data: dict) -> None:
    """Write a JSON file. Crea directorios si no existen."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  📝 Escrito: {path}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_project_root(start: str | None = None) -> str:
    """Encuentra la raíz del proyecto (repo root).

    Los impacted_artifacts.files están escritos relativos al repo root
    (ej: .claude/skills/delta/scripts/spring.py). Por eso find_project_root
    devuelve el repo root, no el project_path del plan.

    Busca .git hacia arriba desde start.
    """
    start = start or os.getcwd()
    start = os.path.abspath(start)

    # Buscar .git hacia arriba
    cwd = start
    for _ in range(10):
        if os.path.exists(os.path.join(cwd, ".git")):
            return cwd
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent

    return start


def get_project_path(chain_dir: str) -> str:
    """Lee project.project_path del plan.json.

    Útil para saber dónde está el código del skill (ej: .claude/skills/delta).
    """
    plan_path = os.path.join(chain_dir, "plan.json")
    if not os.path.exists(plan_path):
        return ""
    try:
        with open(plan_path) as f:
            plan = json.load(f)
        return plan.get("plan", plan).get("project", {}).get("project_path", "")
    except Exception:
        return ""


def _run_script(
    script_name: str, script_args: list[str], timeout: int = 300, capture: bool = True
) -> subprocess.CompletedProcess:
    """Run a delta script (from scripts/) and return the CompletedProcess.

    Captures stdout and stderr by default so the caller can print or
    inspect them. On timeout, prints a warning and returns exit code 2.
    """
    script_path = os.path.join(_SCRIPT_DIR, script_name)
    if not os.path.exists(script_path):
        eprint(f"❌ Script no encontrado: {script_path}")
        return subprocess.CompletedProcess([], returncode=2, stdout="", stderr="")

    is_shell = script_name.endswith(".sh")
    if is_shell:
        cmd = ["bash", script_path] + script_args
    else:
        cmd = [sys.executable, script_path] + script_args

    try:
        r = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=timeout,
        )
        return r
    except subprocess.TimeoutExpired:
        eprint(f"  ⚠️  Timeout ({timeout}s) ejecutando: {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, returncode=2, stdout="", stderr="")
    except FileNotFoundError:
        eprint(f"❌ No se encontró el ejecutable: {cmd[0]}")
        return subprocess.CompletedProcess(cmd, returncode=2, stdout="", stderr="")


def _validate_schema(json_path: str, schema_name: str) -> bool:
    """Validate a JSON file against its schema. Returns True if valid.

    ENFORCEMENT: Missing validator or schema is a blocking error.
    No optional skips. Called after every write to prevent dirty data.
    """
    schema_path = os.path.join(_BASE_DIR, "schema", schema_name)
    validate_script = os.path.join(_SCRIPT_DIR, "validate-schema.py")
    if not os.path.exists(validate_script):
        eprint("  ❌ VALIDATION ENFORCEMENT: validate-schema.py no encontrado")
        return False
    if not os.path.exists(schema_path):
        eprint(f"  ❌ VALIDATION ENFORCEMENT: schema no encontrado: {schema_path}")
        return False
    r = _run_script("validate-schema.py", [json_path, schema_path], timeout=15)
    if r.returncode == 0:
        return True
    # Print the validation errors
    if r.stdout:
        for line in r.stdout.splitlines():
            if any(
                tag in line
                for tag in ("❌", "Mensaje:", "Error", "#1", "#2", "RECOVERY")
            ):
                eprint(f"  {line.strip()}")
    return False


def _extract_project_id(chain_dir: str) -> str | None:
    """Read plan.json and extract project_id."""
    plan_path = os.path.join(chain_dir, "plan.json")
    plan = load_json(plan_path)
    if not plan:
        return None
    pl = plan.get("plan", plan)
    try:
        return pl.get("project", {}).get("project_id") or None
    except Exception:
        return None


def _resolve_project_path(
    project_root: str,
    project_id: str | None,
    path: str,
    project_path: str | None = None,
) -> str:
    """Resolve a path from plan.json to an absolute path.

    Handles multiple formats:
    - projects/cognitive-mcp/cognitive_v2/... (full from project_root)
    - cognitive_v2/... (from projects/<project_id>/)
    - features/... (from projects/<project_id>/cognitive_v2/ or project_root)
    - Explicit project_path from plan.json (relative or absolute)
    """
    # Already absolute
    if os.path.isabs(path):
        return path

    # Try direct from project_root
    direct = os.path.join(project_root, path)
    if os.path.exists(direct):
        return direct

    # Try under explicit project_path if provided
    if project_path:
        if os.path.isabs(project_path):
            explicit = os.path.join(project_path, path)
        else:
            explicit = os.path.join(project_root, project_path, path)
        if os.path.exists(explicit):
            return explicit

    # Try under projects/<project_id>/
    if project_id:
        under_project = os.path.join(project_root, "projects", project_id, path)
        if os.path.exists(under_project):
            return under_project

    # Try under projects/<project_id>/cognitive_v2/
    if project_id:
        under_cognitive = os.path.join(
            project_root, "projects", project_id, "cognitive_v2", path
        )
        if os.path.exists(under_cognitive):
            return under_cognitive

    # Default: return direct path (may not exist)
    return direct
