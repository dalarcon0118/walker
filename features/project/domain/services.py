"""
domain/services.py — Domain services (backward-compat + new pure impls).

Preserves original API signatures for callers that haven't migrated.
New implementations with return-pattern available as separate functions.
"""
from __future__ import annotations

import os
import re


# ── ProjectId validation (backward-compat: returns bool) ──

def validate_project_id(value: str) -> bool:
    """Validate project_id format. Returns bool (backward-compat)."""
    if not value or not str(value).strip():
        return False
    return bool(re.match(r"^[a-zA-Z0-9_\-]+$", value))


def validate_project_id_checked(value: str) -> tuple[bool, list[str]]:
    """Validate project_id. Returns (is_valid, errors) — return pattern."""
    errors: list[str] = []
    if not value or not str(value).strip():
        errors.append("project_id cannot be empty")
        return False, errors
    if not re.match(r"^[a-zA-Z0-9_\-]+$", value):
        errors.append(f"Invalid project_id '{value}': [a-zA-Z0-9_-]+ only")
        return False, errors
    return True, []


def sanitize_project_id(value: str) -> str:
    """Sanitize a string for use as project_id."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", value)


# ── Path derivation (backward-compat: returns dict) ──

def compute_project_paths(project_root: str) -> dict[str, str]:
    """Compute project paths from project_root. Returns dict (backward-compat).

    ADR-WALKER-061 F6: el flujo del sprint es ÚNICO y global (SSOT del motor).
    El manifiesto canónico se resuelve desde la config central — NO hay
    workflow.v3 por proyecto ni fallbacks (cero deuda técnica). Los paths de
    workflow derivan de ``get_workflow_manifest_dir/path`` (config central),
    no de ``project_root``.
    """
    from shared.core.engine.infrastructure.config import (
        get_workflow_manifest_dir,
        get_workflow_manifest_path,
    )

    data_dir = os.path.join(project_root, "data")
    delta_dir = os.path.join(project_root, "delta")
    # Manifiesto canónico global (config central): data/workflow del motor.
    workflow_dir = get_workflow_manifest_dir()
    workflow_json = get_workflow_manifest_path()

    return {
        "project_root": project_root,
        "data_dir": data_dir,
        "delta_dir": delta_dir,
        "workflow_dir": workflow_dir,
        "traces_dir": os.path.join(data_dir, "traces"),
        "cpg_path": os.path.join(data_dir, "cpg.json.gz"),
        "ckg_path": os.path.join(data_dir, "ckg.json.gz"),
        # project.json canónico: <project_root>/data/project.json (todo en data/).
        # Antes apuntaba a <project_root>/project.json (inexistente en el layout
        # cognitivo), lo que dejaba el manifest vacío (el build fingerprint
        # cubría todo el repo y nunca coincidía con el watcher).
        "project_json_path": os.path.join(data_dir, "project.json"),
        "workflow_json_path": workflow_json,
    }


# ── Sprint state path ──

def sprint_state_path(chain_dir: str) -> str:
    """Path to .workflow-state.json (engine state) for a sprint."""
    return os.path.join(chain_dir, ".workflow-state.json")
