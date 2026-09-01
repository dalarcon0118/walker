"""shared_kernel.config — Config del walker (port mínimo de engine config).

Lee config/config.json (base) con la misma semántica de get(key, default)
que el engine usa. El engine config completo se porta en Fase 3; esta versión
kernel cubre las claves de rutas/behavior que path_resolver y libs necesitan.
"""

from __future__ import annotations

import json
from typing import Any

from fast_mcp.shared_kernel.paths import _read_config_file


def get(key: str, default: Any = None) -> Any:
    """Valor de configuración con cascada proyecto→walker (igual que engine)."""
    cfg = _read_config_file()
    return cfg.get(key, default)


def get_workflow_manifest_path() -> str:
    """Ruta ABSOLUTA al workflow.json canónico del motor (SSOT).

    Key 'workflow.manifest' (env WALKER_WORKFLOW_MANIFEST o config).
    Sin config explícita → <walker_root>/data/workflow/workflow.json.
    """
    import os
    from pathlib import Path

    val = str(get("workflow.manifest", "") or os.environ.get("WALKER_WORKFLOW_MANIFEST", ""))
    if val:
        return val
    return str(Path(_config_walker_root()) / "data" / "workflow" / "workflow.json")


def get_workflow_manifest_dir() -> str:
    """Directorio del manifiesto canónico (dirname de workflow.manifest)."""
    import os

    return os.path.dirname(get_workflow_manifest_path())


def _config_walker_root() -> str:
    """Raíz walker para config (evita ciclo: paths._read_config_file la usa)."""
    from pathlib import Path

    candidate = Path(__file__).resolve().parent.parent.parent  # kernel→fast_mcp→raíz
    for _ in range(8):
        if (candidate / "config" / "config.json").is_file():
            return str(candidate.resolve())
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return str(Path.cwd().resolve())
