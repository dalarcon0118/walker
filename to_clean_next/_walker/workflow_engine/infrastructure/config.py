"""
infrastructure/config.py — Configuracion del Walker v3.

Soporta config por archivo (config/config.json del proyecto) y variables de entorno.
Las env vars tienen prioridad sobre el archivo de config.

Fuentes de verdad:
  - <project_root>/config/config.json  → config del walker (projects_dir, etc.)
  - project.json                        → manifiesto por proyecto (repo_path, code_paths, etc.)

Keys con anidamiento (dot-notation):
  delta.curvature_budget, tools.ast_grep_bin, quality.cyclo_max, scan.ignored_dirs
"""
from __future__ import annotations

import json
import os
from typing import Any

ENV_PREFIX = "WALKER_"


def _resolve_walker_root() -> str:
    """Resuelve la raíz del proyecto walker.
    
    Delega en path_resolver.get_walker_root() (import lazy para evitar circular).
    """
    from _walker.shared.path_resolver import get_walker_root
    return str(get_walker_root())


CONFIG_FILE = os.path.join(_resolve_walker_root(), "config", "config.json")

_DEFAULTS: dict[str, Any] = {
    # -- Paths planos (projects_dir, sessions_dir, scripts_dir van en resolución dinámica)
    "config_file": CONFIG_FILE,
    "workflow_dir": "workflow.v3",
    "data_dir": "data",
    # -- Identidad --
    "agent_id": "codebuff",
    "session_id": "",
    "port": 8000,
    # -- Delta pipeline --
    "delta.curvature_budget": 0.50,
    # -- Tools --
    "tools.ast_grep_bin": "sg",
    "tools.qlty_bin": "qlty",
    # -- Quality thresholds --
    "quality.cyclo_max": 30,
    "quality.lcom_max": 10,
    "quality.ce_max": 5,
    "quality.cc_per_func_max": 10,
    "quality.dup_blocks_max": 2,
    "quality.security_high_max": 0,
    "quality.hallucinated_imports_max": 0,
    "quality.coverage_min": 80,
    # -- Scan --
    "scan.ignored_dirs": [
        "node_modules", ".git", "__pycache__", "venv", ".venv",
        ".env", "dist", "build", ".next", ".cognitive",
    ],
}


def _deep_get(d: dict, dotted_key: str, default: Any = None) -> Any:
    """Accede a un valor anidado usando notación de puntos.
    
    Ej: _deep_get({"a": {"b": 1}}, "a.b") → 1
    """
    parts = dotted_key.split(".")
    current = d
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
            if current is None:
                return default
        else:
            return default
    return current


def _load_config_file() -> dict:
    """Carga config desde <project_root>/config/config.json si existe."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _get_env(key: str) -> str | None:
    """Lee variable de entorno WALKER_{key}."""
    env_key = key.upper().replace(".", "_")
    return os.environ.get(f"{ENV_PREFIX}{env_key}")


def get(key: str, default: Any = None) -> Any:
    """Obtiene un valor de config con resolucion: env > file > defaults > dinamico.

    Args:
        key: Nombre de la config (ej: 'sessions_dir', 'delta.curvature_budget').
        default: Valor por defecto si no se encuentra en ninguna fuente.

    Returns:
        Valor de configuracion.
    """
    # 1. Variable de entorno (WALKER_KEY o WALKER_SECTION_KEY para anidados)
    env_val = _get_env(key)
    if env_val is not None:
        return env_val

    # 2. Archivo de config (soporta dot-notation para anidados)
    file_config = _load_config_file()
    if "." in key:
        val = _deep_get(file_config, key)
        if val is not None:
            return val
    else:
        if key in file_config:
            return file_config[key]

    # 3. Defaults planos
    if key in _DEFAULTS and _DEFAULTS[key] is not None:
        return _DEFAULTS[key]

    # 4. Valores dinamicos — delegan en path_resolver (única fuente de verdad)
    if key == "projects_dir":
        cfg_val = file_config.get("projects_dir")
        if cfg_val:
            return cfg_val
        from _walker.shared.path_resolver import get_projects_dir
        return str(get_projects_dir())

    if key == "sessions_dir":
        cfg_val = file_config.get("sessions_dir")
        if cfg_val:
            return cfg_val
        from _walker.shared.path_resolver import get_sessions_dir
        return str(get_sessions_dir())

    if key == "scripts_dir":
        from _walker.shared.path_resolver import get_walker_root
        scripts = str(get_walker_root() / "scripts")
        if os.path.isdir(os.path.join(scripts, "_walker")):
            return scripts
        env_scripts = os.environ.get("WALKER_SCRIPTS_DIR")
        if env_scripts and os.path.isdir(os.path.join(env_scripts, "_walker")):
            return env_scripts
        return scripts

    # 5. Default del parametro
    return default


def set_config(key: str, value: Any) -> dict:
    """Guarda un valor en el archivo de config persistente.

    Args:
        key: Nombre de la config.
        value: Valor a guardar.

    Returns:
        Dict con la config actualizada.
    """
    config = _load_config_file()
    config[key] = value
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    return config


def get_all() -> dict:
    """Retorna toda la config actual (resuelta)."""
    from _walker.shared.path_resolver import (
        get_walker_root, get_cognitive_dir, get_projects_dir,
        get_sessions_dir,
    )
    return {
        "walker_root": str(get_walker_root()),
        "cognitive_dir": str(get_cognitive_dir()),
        "projects_dir": str(get_projects_dir()),
        "sessions_dir": str(get_sessions_dir()),
        "config_file": CONFIG_FILE,
        "workflow_dir": get("workflow_dir"),
        "scripts_dir": get("scripts_dir"),
        "data_dir": get("data_dir"),
        "agent_id": get("agent_id"),
        "port": get("port"),
        "delta.curvature_budget": get("delta.curvature_budget"),
        "tools.ast_grep_bin": get("tools.ast_grep_bin"),
        "tools.qlty_bin": get("tools.qlty_bin"),
        "quality.cyclo_max": get("quality.cyclo_max"),
        "quality.lcom_max": get("quality.lcom_max"),
        "quality.ce_max": get("quality.ce_max"),
        "quality.cc_per_func_max": get("quality.cc_per_func_max"),
        "quality.dup_blocks_max": get("quality.dup_blocks_max"),
        "quality.security_high_max": get("quality.security_high_max"),
        "quality.hallucinated_imports_max": get("quality.hallucinated_imports_max"),
        "quality.coverage_min": get("quality.coverage_min"),
        "scan.ignored_dirs": get("scan.ignored_dirs"),
    }


def reset() -> None:
    """Elimina el archivo de config (vuelve a defaults/env)."""
    if os.path.exists(CONFIG_FILE):
        os.remove(CONFIG_FILE)


# ═══════════════════════════════════════════════════════════════════
# Re-export desde path_resolver (backward compat)
# Las funciones de resolucion de paths viven en _walker/path_resolver.py
# ═══════════════════════════════════════════════════════════════════

from _walker.shared.path_resolver import (
    resolve_project_root as _resolve_project_root,  # noqa: F401 — usado por 42+ importers
    resolve_real_project_dir as _resolve_real_project_dir,  # noqa: F401
    resolve_data_path as resolve_data_path,  # noqa: F401
    resolve_trace_path as resolve_trace_path,  # noqa: F401
)
