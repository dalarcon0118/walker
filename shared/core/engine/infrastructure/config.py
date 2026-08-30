"""
infrastructure/config.py — Configuracion del Walker v3.

Soporta config por archivo (config/config.json del proyecto) y variables de entorno.
Las env vars tienen prioridad sobre el archivo de config.

Fuentes de verdad:
  - <project_root>/config/config.json  → config del walker (projects_dir, etc.)
  - project.json                        → manifiesto por proyecto (repo_path, etc.)

Keys con anidamiento (dot-notation):
  delta.curvature_budget, tools.ast_grep_bin, quality.cyclo_max, scan.ignored_dirs
"""
from __future__ import annotations

import json
import os
from typing import Any

from shared.core.engine.domain.ports import IConfig

ENV_PREFIX = "WALKER_"


def _resolve_walker_root() -> str:
    """Resuelve la raíz del proyecto walker (autocontenido, sin path_resolver).

    Sube desde el CWD hasta encontrar un directorio con `.cognitive`. El
    layout exacto del host lo provee el adapter IConfig del host
    (app/config.py); aquí solo el fallback host-agnóstico.
    """
    from pathlib import Path

    cwd = Path.cwd()
    for p in [cwd, *cwd.parents]:
        if (p / ".cognitive").is_dir():
            return str(p)
    return str(cwd)


CONFIG_FILE = os.path.join(_resolve_walker_root(), "config", "config.json")

_DEFAULTS: dict[str, Any] = {
    # -- Paths planos (projects_dir, sessions_dir, scripts_dir van en resolución dinámica)
    "config_file": CONFIG_FILE,
    "data_dir": "data",
    # -- Registro global de proyectos gestionados (ADR-WALKER-061 F6-fix) --
    # Relativo a walker_root si no es absoluto. Env: WALKER_REGISTRY_PROJECTS_FILE
    "registry.projects_file": "data/projects.json",
    # -- Identidad --
    "agent_id": "codebuff",
    "session_id": "",
    "port": 8000,
    # -- Delta pipeline --
    "delta.curvature_budget": 0.50,
    # -- Tools --
    "tools.ast_grep_bin": "sg",
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
    # -- Memory storage (ADR-WALKER-061: la conexión es configuración) --
    # ADR-WALKER-063 (corte directo): sqlite (v2) es el ÚNICO backend runtime;
    # 'file' (legacy .json.gz) NO es un valor válido — fail-fast en
    # cognitive_graph.config (solo export/migración/tests). memory.uri NO
    # tiene default: el backend 'sqlite' lo construye desde la raíz de
    # memorias resuelta; un backend remoto sin uri es error (fail-fast).
    "memory.backend": "sqlite",
    "memory.db": "walker",
    "memory.prefix": "",
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
    if key == "workflow.manifest":
        # Manifiesto canónico del flujo del sprint (SSOT del motor).
        # Sin config explícita → <walker_root>/data/workflow/workflow.json.
        cfg_val = _deep_get(file_config, key)
        if cfg_val:
            return cfg_val
        return str(
            __import__("pathlib").Path(_resolve_walker_root())
            / "data" / "workflow" / "workflow.json"
        )

    if key == "projects_dir":
        cfg_val = file_config.get("projects_dir")
        if cfg_val:
            return cfg_val
        return str(
            __import__("pathlib").Path(_resolve_walker_root())
            / ".cognitive" / "projects"
        )

    if key == "sessions_dir":
        cfg_val = file_config.get("sessions_dir")
        if cfg_val:
            return cfg_val
        return str(
            __import__("pathlib").Path(_resolve_walker_root())
            / ".cognitive" / "sessions"
        )

    if key == "scripts_dir":
        scripts = str(__import__("pathlib").Path(_resolve_walker_root()) / "scripts")
        if os.path.isdir(os.path.join(scripts, "_walker")):
            return scripts
        env_scripts = os.environ.get("WALKER_SCRIPTS_DIR")
        if env_scripts and os.path.isdir(os.path.join(env_scripts, "_walker")):
            return env_scripts
        return scripts

    # 5. Default del parametro
    return default


def get_projects_registry_path() -> str:
    """Ruta ABSOLUTA al registro global de proyectos (data/projects.json).

    SSOT: key 'registry.projects_file' (env WALKER_REGISTRY_PROJECTS_FILE o
    config/config.json). Relativo → se resuelve contra walker_root.
    """
    val = str(get("registry.projects_file", "data/projects.json"))
    p = __import__("pathlib").Path(val)
    if not p.is_absolute():
        p = __import__("pathlib").Path(_resolve_walker_root()) / p
    return str(p)


def get_workflow_manifest_path() -> str:
    """Ruta ABSOLUTA al manifiesto canónico del flujo del sprint (workflow.json).

    SSOT: key 'workflow.manifest' (env WALKER_WORKFLOW_MANIFEST o
    config/config.json). Sin config explícita → el canónico del motor
    (<walker_root>/data/workflow/workflow.json). ÚNICA fuente.
    """
    val = str(get("workflow.manifest", ""))
    if not val:
        return str(
            __import__("pathlib").Path(_resolve_walker_root())
            / "data" / "workflow" / "workflow.json"
        )
    p = __import__("pathlib").Path(val)
    if not p.is_absolute():
        p = __import__("pathlib").Path(_resolve_walker_root()) / p
    return str(p)


def get_workflow_manifest_dir() -> str:
    """Directorio del manifiesto canónico (dirname de workflow.manifest)."""
    return os.path.dirname(get_workflow_manifest_path())


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
    from pathlib import Path

    root = Path(_resolve_walker_root())
    return {
        "walker_root": str(root),
        "cognitive_dir": str(root / ".cognitive"),
        "projects_dir": str(root / ".cognitive" / "projects"),
        "sessions_dir": str(root / ".cognitive" / "sessions"),
        "config_file": CONFIG_FILE,
        "scripts_dir": get("scripts_dir"),
        "data_dir": get("data_dir"),
        "agent_id": get("agent_id"),
        "port": get("port"),
        "delta.curvature_budget": get("delta.curvature_budget"),
        "tools.ast_grep_bin": get("tools.ast_grep_bin"),
        "quality.cyclo_max": get("quality.cyclo_max"),
        "quality.lcom_max": get("quality.lcom_max"),
        "quality.ce_max": get("quality.ce_max"),
        "quality.cc_per_func_max": get("quality.cc_per_func_max"),
        "quality.dup_blocks_max": get("quality.dup_blocks_max"),
        "quality.security_high_max": get("quality.security_high_max"),
        "quality.hallucinated_imports_max": get("quality.hallucinated_imports_max"),
        "quality.coverage_min": get("quality.coverage_min"),
        "scan.ignored_dirs": get("scan.ignored_dirs"),
        "memory.backend": get("memory.backend"),
        "memory.uri": get("memory.uri", ""),
        "memory.db": get("memory.db"),
        "memory.prefix": get("memory.prefix"),
        "registry.projects_file": get_projects_registry_path(),
        "workflow.manifest": get_workflow_manifest_path(),
    }


def reset() -> None:
    """Elimina el archivo de config (vuelve a defaults/env)."""
    if os.path.exists(CONFIG_FILE):
        os.remove(CONFIG_FILE)


# ═══════════════════════════════════════════════════════════════════
# ConfigAdapter — puerto IConfig del dominio
# ═══════════════════════════════════════════════════════════════════


class ConfigAdapter(IConfig):
    """Adapter del puerto IConfig del dominio sobre la config del engine.

    Inyectado en domain.ports_registry por el composition root para que
    domain/session.py resuelva 'sessions_dir' sin importar infrastructure.
    """

    def get(self, key: str, default: Any = None) -> Any:
        """Resuelve una clave de config (dot-notation) delegando en get()."""
        return get(key, default)

