"""infrastructure.layout — Layout cognitivo de un proyecto (SSOT de rutas).

Port de app/path_resolver.get_project_dir/get_data_dir. La identidad del
proyecto vive en el manifiesto (project.json dentro del layout).
"""

from __future__ import annotations

import json
from pathlib import Path

from fast_mcp.features.project.infrastructure.registry_store import RegistryStore
from fast_mcp.shared_kernel import paths

STATE_FILE = ".workflow-state.json"


class ProjectLayout:
    """Resolución de directorios cognitivos — root inyectable (tests)."""

    def __init__(self, store: RegistryStore, root: Path | None = None) -> None:
        self._store = store
        self._root = root

    def cognitive_dir(self, pid: str, repo_path: str = "") -> Path:
        """El repositorio del proyecto: <repo>/.cognitive/projects/<pid>/. (o bajo la raíz)."""
        repo = repo_path or self._store.resolve(pid).get("repo_path", "")
        if repo:
            cog = Path(repo) / ".cognitive" / "projects" / pid
            if cog.is_dir():
                return cog
        base = self._root if self._root is not None else paths.walker_root()
        return base / ".cognitive" / "projects" / pid

    def data_dir(self, pid: str, repo_path: str = "") -> Path:
        """Datos del proyecto: <cognitive_dir>/data/ (donde vive project.json)."""
        return self.cognitive_dir(pid, repo_path) / "data"

    def find_sprint_state(self, data_dir: Path) -> dict:
        """Estado .workflow-state.json del sprint activo (más reciente), {} si no hay."""
        spring_root = data_dir / "spring"
        if not spring_root.is_dir():
            return {}
        best: tuple[float, dict] = (0.0, {})
        for sprint_dir in spring_root.iterdir():
            if not sprint_dir.is_dir():
                continue
            state_path = sprint_dir / STATE_FILE
            if not state_path.is_file():
                continue
            try:
                with open(state_path) as f:
                    state = json.load(f)
            except Exception:  # noqa: BLE001 - fail-open
                continue
            mtime = sprint_dir.stat().st_mtime
            if mtime > best[0]:
                best = (mtime, state)
        return best[1]
