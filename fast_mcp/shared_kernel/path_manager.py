"""shared_kernel.path_manager — PathManager: única política de rutas del workflow.

SRP: resolver la ruta del manifiesto `workflow.json` (SSOT del concepto).

MODELO (project.md § 'Configuración del workflow: GLOBAL del motor'):
el workflow es configuración GLOBAL del motor — un solo SSOT en el root del
walker, compartido por TODOS los agentes. NO es por-proyecto: los proyectos
NO llevan su propio workflow.json (sería duplicación de SSOT, anti-patrón
ADR-047). `chain_dir` identifica el proyecto para ESTADO y MEMORIA (CPG/CKG/
DeltaGraph/TraceGraph + state), NUNCA para cargar el workflow.

Cascada de resolución (INV-016 fail-fast: decidir explícitamente, no inventar):

  1. Override explícito: env `WALKER_WORKFLOW_MANIFEST` o config key
     `workflow.manifest`. Ruta absoluta → tal cual; relativa → resuelta
     contra la raíz del walker. Si el archivo no existe, el loader falla
     ruidosamente sobre esa ruta exacta — nunca fallback silencioso.
  2. Canónico: <walker_root>/mcp/data/workflow/workflow.json.
  3. None si el canónico no existe y no hay override (caller decide el error
     accionable). NO se buscan candidatos dentro del chain_dir del proyecto.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Mapping

from fast_mcp.shared_kernel.paths import get_walker_root


class PathManager:
    """Única responsabilidad: resolver la ruta del manifiesto workflow.json.

    Sin estado mutable: env, config_get y engine_dir son inyectables para
    testabilidad; en producción usan os.environ, shared_kernel.config y
    paths.get_engine_workflow_dir().
    """

    ENV_OVERRIDE = "WALKER_WORKFLOW_MANIFEST"
    CONFIG_KEY = "workflow.manifest"

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        config_get: Callable[[str], str] | None = None,
        engine_dir: Path | None = None,
    ) -> None:
        """env/config_get/engine_dir: inyectables para tests. None → fuentes
        reales (os.environ, shared_kernel.config, paths.get_engine_workflow_dir).
        """
        self._env = env
        self._config_get = config_get
        self._engine_dir = engine_dir

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

    def _normalized_override(self) -> Path | None:
        """Override normalizado: absoluto tal cual; relativo → contra walker_root."""
        override = self.explicit_override()
        if not override:
            return None
        path = Path(override)
        if not path.is_absolute():
            path = get_walker_root() / path
        return path

    # ── Resolución GLOBAL del workflow del motor ─────────────────────────

    def engine_manifest(self) -> Path:
        """workflow.json del motor: override explícito normalizado o canónico.

        El override se honra TAL CUAL aunque el archivo no exista (fail-fast:
        el loader falla ruidosamente sobre esa ruta exacta — sin fallback
        silencioso al canónico).
        """
        override = self._normalized_override()
        if override is not None:
            return override
        if self._engine_dir is not None:
            return self._engine_dir / "workflow.json"
        from fast_mcp.shared_kernel.paths import get_engine_workflow_dir

        return get_engine_workflow_dir() / "workflow.json"

    def engine_workflow_dir(self) -> Path:
        """Directorio del workflow del motor (dirname del manifest resuelto).

        Override-aware: con override explícito es el dirname del override;
        sin override, el directorio canónico inyectado o
        paths.get_engine_workflow_dir().
        """
        return self.engine_manifest().parent

    def resolve_workflow_manifest(self) -> Path | None:
        """Ruta efectiva del workflow GLOBAL del motor.

        1. Override explícito → se retorna siempre (existencia la valida el
           loader, ruidosamente).
        2. Canónico <walker_root>/mcp/data/workflow/workflow.json → si existe.
        3. None → el caller decide el error accionable.
        """
        override = self._normalized_override()
        if override is not None:
            return override
        canonical = self.engine_manifest()
        return canonical if canonical.is_file() else None
