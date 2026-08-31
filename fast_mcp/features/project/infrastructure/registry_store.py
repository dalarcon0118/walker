"""infrastructure.registry_store — I/O del registro global de proyectos.

Port de app/orchestration/prepare_project_service._register_in_global_registry
y app/path_resolver (resolución git-like). Formato SSOT idéntico al legacy:

    {"projects": {pid: {project_id, name, repo_path, active,
                        registered_at, last_accessed}}, "updated_at": ...}
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fast_mcp.shared_kernel import paths


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RegistryStore:
    """Registro global de proyectos (data/projects.json) — root inyectable."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root

    def read(self) -> dict:
        """Lee el registro; {} si no existe o es inválido (fail-open visible)."""
        path = paths.registry_path(self._root)
        if not path.exists():
            return {}
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:  # noqa: BLE001 - fail-open; el caller loggea/warnea
            return {}

    def register(self, pid: str, name: str, repo_path: str) -> Path:
        """Registra/actualiza un proyecto (último registro gana, igual que legacy)."""
        registry = self.read()
        now = _now()
        projects = registry.setdefault("projects", {})
        existing = projects.get(pid, {})
        projects[pid] = {
            "project_id": pid,
            "name": name,
            "repo_path": repo_path,
            "active": True,
            "registered_at": existing.get("registered_at", now),
            "last_accessed": now,
        }
        registry["updated_at"] = now
        path = paths.registry_path(self._root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(registry, f, indent=2, ensure_ascii=False)
        return path

    def resolve(self, pid: str) -> dict:
        """Entrada del registro para un project_id ({} si no está)."""
        return self.read().get("projects", {}).get(pid, {})

    def resolve_from_cwd(self, start: str | None = None) -> str:
        """Git-like: project_id desde el CWD (o `start`).

        1. El directorio cognitivo de proyectos más cercano caminando hacia
           arriba (como git encuentra .git/).
        2. Fallback: el proyecto del registro cuyo repo_path contenga `start`.
        """
        base = Path(start or os.getcwd()).resolve()
        for _ in range(10):
            cog = base / ".cognitive"
            if cog.is_dir():
                projects_dir = cog / "projects"
                if projects_dir.is_dir():
                    for entry in sorted(projects_dir.iterdir()):
                        if (entry / "data" / "project.json").is_file():
                            return entry.name
            parent = base.parent
            if parent == base:
                break
            base = parent
        start_str = str(Path(start or os.getcwd()).resolve())
        for pid, info in self.read().get("projects", {}).items():
            repo = info.get("repo_path", "")
            if repo and start_str.startswith(repo):
                return pid
        return ""
