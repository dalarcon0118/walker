"""cli/utils.py — Utilidades compartidas para el CLI.

Responsabilidad: funciones auxiliares usadas por todos los comandos CLI.
_Safe serializa objetos Python a dicts JSON-compatibles.
_output imprime resultados JSON.
_get_api crea WorkflowAPI para un proyecto/sprint.
_ensure_qlty_for_project inicializa qlty si es necesario.
"""
from __future__ import annotations
import json
import os
import subprocess
from typing import Any

from features.engine.application.workflow_api import WorkflowAPI
from libs.path_resolver import get_repo_path, read_project_manifest


def _safe(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return {k: _safe(v) for k, v in obj.__dict__.items() if not k.startswith("_")}
    if isinstance(obj, (list, tuple)):
        return [_safe(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _safe(v) for k, v in obj.items()}
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)


def _output(result: dict) -> None:
    print(json.dumps(_safe(result), indent=2, ensure_ascii=False))


def _get_api(project_id: str = "", sprint_hint: str = "") -> WorkflowAPI:
    """Crea WorkflowAPI para un proyecto/sprint específico."""
    from bootstrap.cli.project import _find_sprint
    chain_dir = _find_sprint(project_id, sprint_hint) if project_id else None
    return WorkflowAPI(chain_dir=chain_dir, project_id=project_id)


def _ensure_qlty_for_project(project_dir: str) -> bool:
    """Ensure qlty is initialized for the project."""
    repo_root = get_repo_path(os.path.basename(project_dir))
    if not repo_root or not os.path.isdir(repo_root):
        repo_root = os.path.normpath(os.path.join(project_dir, "..", "..", ".."))

    qlty_dir = os.path.join(repo_root, ".qlty")
    if os.path.isdir(qlty_dir):
        return True

    manifest = read_project_manifest(os.path.basename(project_dir))
    qlty_enabled = (
        manifest.get("project", manifest)
        .get("quality_preferences", {})
        .get("qlty", {})
        .get("enabled", True)
    )
    if not qlty_enabled:
        return False

    print("  ⚙️  Inicializando qlty para análisis de código...")
    try:
        r = subprocess.run(
            ["qlty", "init", "--yes"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode == 0:
            print("  ✅ qlty inicializado correctamente")
            return True
        else:
            print(f"  ⚠️  qlty init falló: {r.stderr[:100]}")
            return False
    except FileNotFoundError:
        print("  ⚠️  qlty no instalado. Instala con: curl -fsSL https://qlty.sh | sh")
        return False
    except subprocess.TimeoutExpired:
        print("  ⚠️  qlty init timed out")
        return False
