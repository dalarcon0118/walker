"""
infrastructure/repositories.py — File-based ProjectRepository implementation.

Implements the ProjectRepository port (domain/ports.py).
Pure IO: reads/writes project.json from .cognitive/projects/<pid>/.

DISTINCIÓN DE CONCEPTOS (features/project):
  - Este repo opera sobre el **MANIFIESTO del proyecto**
    (.cognitive/{project_id}/data/project.json): vive dentro del directorio
    cognitivo del proyecto y describe SU identidad (name, language, repo_path,
    code_paths). Es el "project.json del proyecto" que se está trabajando.
  - NO confundir con el **REGISTRO GLOBAL** (data/projects.json en la raíz
    del walker): es el índice de TODOS los proyectos que el walker administra
    (project_id → repo_path, active, last_accessed). Lo gestiona
    ProjectService (application/service.py), NO este repositorio.
  - Tampoco confundir con **ProjectPaths** (domain/values.py): es un
    utilitario de RESOLUCIÓN de rutas calculadas (chain_dir, project_root,
    data_dir, ...), no un archivo ni un índice persistido.

Return-pattern: every method returns (result, errors) tuple.
Does NOT import the application layer (SRP compliance).
"""

from __future__ import annotations

import json
import os

from ..domain.entities import Project
from ..domain.values import ProjectId, ProjectManifest
from ..domain.ports import ProjectRepository


class CognitiveProjectRepository(ProjectRepository):
    """File-based repo: .cognitive/projects/<pid>/project.json."""

    # ── ProjectRepository interface ──

    def save(self, project: Project) -> tuple[Project | None, list[str]]:
        """Persist a Project aggregate to project.json.

        Returns (saved, errors).
        """
        pid_str = project.project_id.value
        manifest_path = project.paths.project_json_path if project.paths else ""

        if not manifest_path:
            manifest_path = _resolve_manifest_path(pid_str)
            if not manifest_path:
                return None, [f"No project.json path for '{pid_str}'"]

        data = {
            "project": {
                "meta": {
                    "project_id": pid_str,
                    "version": "1.0",
                    "updated_at": _now_iso(),
                },
                "identity": {
                    "name": project.manifest.name or pid_str,
                    "language": project.manifest.language,
                    "framework": project.manifest.framework,
                },
                "location": {
                    "repo_path": project.manifest.repo_path,
                    "code_paths": project.manifest.code_paths,
                },
            }
        }

        try:
            os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
            with open(manifest_path, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return project, []
        except (OSError, IOError) as e:
            return None, [str(e)]

    def load(self, project_id: ProjectId | str) -> tuple[Project | None, list[str]]:
        """Load a Project by id. Returns (project, errors)."""
        pid = (
            project_id
            if isinstance(project_id, ProjectId)
            else ProjectId(str(project_id))
        )
        pid_str = pid.value

        manifest_path = _resolve_manifest_path(pid_str)
        if not manifest_path:
            return None, [f"Project not found: '{pid_str}'"]

        try:
            with open(manifest_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            return None, [f"Cannot read manifest for '{pid_str}': {e}"]

        manifest, errors = ProjectManifest.create(data)
        if errors:
            return None, errors

        project = Project(project_id=pid, manifest=manifest)
        return project, []

    def list_all(self) -> tuple[list[Project], list[str]]:
        """List all registered projects. Returns (projects, errors)."""
        from libs.path_resolver import get_projects_dir

        projects_dir = get_projects_dir()
        if not projects_dir.is_dir():
            return [], []

        result: list[Project] = []
        errors: list[str] = []
        for entry in sorted(projects_dir.iterdir()):
            if not entry.is_dir():
                continue
            pid = ProjectId(entry.name)
            project, proj_errors = self.load(pid)
            if project:
                result.append(project)
            else:
                errors.extend(proj_errors)

        return result, errors

    def exists(self, project_id: ProjectId | str) -> tuple[bool, list[str]]:
        """Check if a project exists. Returns (exists, errors)."""
        pid = (
            project_id
            if isinstance(project_id, ProjectId)
            else ProjectId(str(project_id))
        )
        manifest_path = _resolve_manifest_path(pid.value)
        return bool(manifest_path), []


# ── Helpers ──


def _resolve_manifest_path(project_id: str) -> str | None:
    """Find project.json for a given project_id. No IO to application layer."""
    from libs.path_resolver import get_project_dir

    proj_dir = get_project_dir(project_id)
    if not proj_dir.is_dir():
        return None

    for candidate in (
        proj_dir / "data" / "project.json",
        proj_dir / "project.json",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _now_iso() -> str:
    """Current ISO 8601 timestamp."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
