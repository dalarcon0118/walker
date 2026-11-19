"""shared_kernel.config — Config del walker (port mínimo de engine config).

Lee config/config.json (base) con la misma semántica de get(key, default)
que el engine usa. El engine config completo se porta en Fase 3; esta versión
kernel cubre las claves de rutas/behavior que path_resolver y libs necesitan.
"""

from __future__ import annotations

from typing import Any

from fast_mcp.shared_kernel.paths import _read_config_file


def get(key: str, default: Any = None) -> Any:
    """Valor de configuración con cascada proyecto→walker (igual que engine)."""
    cfg = _read_config_file()
    return cfg.get(key, default)


def get_workflow_manifest_path() -> str:
    """(Compat) Delega en PathManager — único SSOT de rutas del workflow.

    Política real: shared_kernel.path_manager.PathManager.engine_manifest()
    (override env WALKER_WORKFLOW_MANIFEST / config 'workflow.manifest' →
    default <walker_root>/data/workflow/workflow.json).
    """
    from fast_mcp.shared_kernel.path_manager import PathManager

    return str(PathManager().engine_manifest())


def get_workflow_manifest_dir() -> str:
    """(Compat) Delega en PathManager.engine_workflow_dir() (SSOT único)."""
    from fast_mcp.shared_kernel.path_manager import PathManager

    return str(PathManager().engine_workflow_dir())
