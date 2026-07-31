"""
domain/services.py — Project domain services (pure logic, no IO).

Contiene operaciones de dominio que no requieren acceso a disco:
  - Validacion de project_id
  - Derivacion de paths (dado un project_root ya conocido)
  - Calculos sobre estructura de proyecto
"""
from __future__ import annotations

import os
import re
from typing import Any

from .models import ProjectId, ProjectPaths


# ── ProjectId validation ────────────────────────────────────────────────

PROJECT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")


def validate_project_id(value: str) -> bool:
    """Valida que un project_id tenga formato correcto.

    Solo letras, numeros, guiones y underscores.
    """
    return bool(PROJECT_ID_PATTERN.match(value))


def sanitize_project_id(value: str) -> str:
    """Sanitiza un string para usar como project_id."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", value)


# ── Path derivation (pure, given known roots) ────────────────────────────


def compute_project_paths(
    project_root: str,
    chain_dir: str | None = None,
) -> dict[str, str]:
    """Deriva paths canonicos desde un project_root conocido.

    Funcion PURA: recibe paths como entrada, no hace IO.
    """
    data_dir = os.path.join(project_root, "data")
    delta_dir = os.path.join(project_root, "delta")
    workflow_dir = os.path.join(project_root, "workflow.v3")
    traces_dir = os.path.join(data_dir, "traces")

    return {
        "project_root": project_root,
        "data_dir": data_dir,
        "delta_dir": delta_dir,
        "workflow_dir": workflow_dir,
        "traces_dir": traces_dir,
        "cpg_path": os.path.join(data_dir, "cpg.json.gz"),
        "ckg_path": os.path.join(data_dir, "ckg.json.gz"),
        "project_json_path": os.path.join(data_dir, "project.json"),
        "workflow_json_path": os.path.join(workflow_dir, "workflow.json"),
    }


def sprint_state_path(chain_dir: str) -> str:
    """Ruta a sprint-state.json (funcion pura, solo concatena paths)."""
    return os.path.join(chain_dir, "sprint-state.json")


def plan_json_path(chain_dir: str) -> str:
    """Ruta a plan.json (funcion pura, solo concatena paths)."""
    return os.path.join(chain_dir, "plan.json")
