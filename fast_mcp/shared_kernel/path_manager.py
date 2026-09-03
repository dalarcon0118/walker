"""shared_kernel.path_manager — PathManager: única política de rutas del workflow.

SRP: resolver la ruta del manifiesto `workflow.json` (SSOT del concepto).

Cascada de resolución (INV-016 fail-fast: decidir explícitamente, no inventar):

  1. Override explícito: env `WALKER_WORKFLOW_MANIFEST` o config key
     `workflow.manifest` (`config/config.json`). Gana siempre y se retorna
     TAL CUAL (ruta relativa → resuelta contra chain_dir): si el archivo no
     existe, el loader falla ruidosamente sobre esa ruta exacta — nunca se
     degrada silenciosamente al candidato canónico.
  2. Canónico:  {chain_dir}/mcp/data/workflow/workflow.json
  3. Flat:      {chain_dir}/workflow.json
  4. None       (el caller decide el fallo con su propio error accionable)

La ubicación legacy `{chain_dir}/data/workflow/` fue ELIMINADA de la cascada:
era una "ubicación fantasma" que legitimaba datos parásitos en runtime
(INV-016). PathManager NO la busca.

Para el manifiesto canónico del PROPIO walker (no de un proyecto target),
usar `engine_manifest()` / `engine_workflow_dir()` — delegan en
`paths.get_engine_workflow_dir()` (raíz detectada por el marker `features/`).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Mapping

from fast_mcp.shared_kernel.paths import get_engine_workflow_dir


class PathManager:
    """Única responsabilidad: resolver rutas del manifiesto workflow.json.

    Sin estado mutable: la configuración es inyectable (env + config_get)
    para testabilidad; en producción usa os.environ y shared_kernel.config.
    """

    ENV_OVERRIDE = "WALKER_WORKFLOW_MANIFEST"
    CONFIG_KEY = "workflow.manifest"

    CANONICAL_PARTS = ("mcp", "data", "workflow", "workflow.json")

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        config_get: Callable[[str], str] | None = None,
    ) -> None:
        """env: mapping alternativo a os.environ (tests). config_get: getter
        alternativo de config por clave (tests). None → fuentes reales."""
        self._env = env
        self._config_get = config_get

    # ── Fuentes del override explícito ──────────────────────────────────

    def _env_value(self) -> str:
        if self._env is not None:
            return str(self._env.get(self.ENV_OVERRIDE, "") or "")
        return str(os.environ.get(self.ENV_OVERRIDE, "") or "")

    def _config_value(self) -> str:
        if self._config_get is not None:
            return str(self._config_get(self.CONFIG_KEY) or "")
        try:
            from fast_mcp.shared_kernel.config import get as _config_get

            return str(_config_get(self.CONFIG_KEY, "") or "")
        except Exception:
            return ""

    def explicit_override(self) -> str:
        """Override explícito (env > config). Vacío si ninguno define."""
        return self._env_value() or self._config_value()

    # ── Resolución por proyecto (engine: submit/transition/status) ──────

    def resolve_for_project(self, chain_dir: str | os.PathLike) -> Path | None:
        """Ruta workflow.json del proyecto target.

        1. Override explícito (env/config) — se honra tal cual: si no existe
           el archivo, el caller falla ruidosamente sobre esa ruta (fail-fast,
           sin fallback silencioso al canónico).
        2. Canónico {chain_dir}/mcp/data/workflow/workflow.json.
        3. Flat {chain_dir}/workflow.json.
        4. None — sin workflow en el proyecto (caller decide el error).
        """
        chain = Path(chain_dir)

        override = self.explicit_override()
        if override:
            path = Path(override)
            if not path.is_absolute():
                path = chain / path
            return path

        canonical = chain.joinpath(*self.CANONICAL_PARTS)
        if canonical.is_file():
            return canonical

        flat = chain / "workflow.json"
        if flat.is_file():
            return flat

        return None

    # ── Manifiesto canónico del PROPIO walker ───────────────────────────

    def engine_manifest(self) -> Path:
        """workflow.json canónico del motor (el propio walker).

        Override explícito (env/config) o el SSOT del repo:
        <walker_root>/mcp/data/workflow/workflow.json — vía
        paths.get_engine_workflow_dir() (raíz por marker features/).
        """
        override = self.explicit_override()
        if override:
            return Path(override)
        return get_engine_workflow_dir() / "workflow.json"

    def engine_workflow_dir(self) -> Path:
        """Directorio del manifiesto canónico del motor (dirname)."""
        return self.engine_manifest().parent
