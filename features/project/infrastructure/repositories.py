"""
infrastructure/repositories.py — Implementaciones concretas de ProjectRepository.

SRP: Unica responsabilidad = leer/escribir proyectos desde el sistema de archivos.
SSOT: Delega en libs.path_resolver para descubrir paths canonicos.
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..domain.models import ProjectId, ProjectPaths, Project, ProjectRepository


class CognitiveProjectRepository(ProjectRepository):
    """Repositorio de proyectos basado en .cognitive/projects/<pid>/.

    Lee project.json del directorio data/ del proyecto.
    Escribe project.json cuando se guarda un proyecto.
    """

    def load(self, project_id: ProjectId | str) -> Project | None:
        """Carga un proyecto desde .cognitive/projects/<pid>/.

        Args:
            project_id: ProjectId o string con el ID del proyecto.

        Returns:
            Project si existe, None si no.
        """
        pid = project_id if isinstance(project_id, ProjectId) else ProjectId(project_id)
        return self._load_from_path(pid)

    def _load_from_path(self, project_id: ProjectId) -> Project | None:
        """Carga proyecto desde su directorio en projects/."""
        from libs.path_resolver import get_project_dir, get_data_dir, get_delta_dir

        pid_str = project_id.value
        proj_dir = str(get_project_dir(pid_str))

        # Buscar project.json
        manifest_path = os.path.join(proj_dir, "data", "project.json")
        if not os.path.exists(manifest_path):
            manifest_path = os.path.join(proj_dir, "project.json")
            if not os.path.exists(manifest_path):
                return None

        try:
            with open(manifest_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

        proj_data = data.get("project", data)
        identity = proj_data.get("identity", {})
        loc = proj_data.get("location", {})

        # Construir paths
        from ..application.service import ProjectService
        try:
            active_sprint = ProjectService.find_active_sprint(pid_str)
        except Exception:
            active_sprint = None

        if active_sprint:
            paths = ProjectService.resolve_paths(active_sprint)
        else:
            data_dir = str(get_data_dir(pid_str))
            delta_dir = str(get_delta_dir(pid_str))
            traces_dir = os.path.join(data_dir, "traces")
            paths = ProjectPaths(
                chain_dir="",
                project_root=proj_dir,
                project_id=pid_str,
                data_dir=data_dir,
                delta_dir=delta_dir,
                workflow_dir=os.path.join(proj_dir, "workflow.v3"),
                traces_dir=traces_dir,
                cpg_path=os.path.join(data_dir, "cpg.json.gz"),
                ckg_path=os.path.join(data_dir, "ckg.json.gz"),
                project_json_path=manifest_path,
                workflow_json_path=os.path.join(proj_dir, "workflow.v3", "workflow.json"),
                repo_path=loc.get("repo_path", ""),
                code_paths=tuple(loc.get("code_paths", [])),
                cognitive_dir="",
                walker_root="",
            )

        return Project(
            project_id=project_id,
            name=identity.get("name", pid_str),
            language=identity.get("language", ""),
            framework=identity.get("framework", ""),
            repo_path=loc.get("repo_path", ""),
            code_paths=loc.get("code_paths", []),
            paths=paths,
            active_sprint_id=os.path.basename(active_sprint) if active_sprint else "",
        )

    def save(self, project: Project) -> None:
        """Persiste un proyecto a project.json.

        Crea el directorio data/ si no existe.
        """
        if not project.paths or not project.paths.project_json_path:
            raise ValueError("Project sin paths resueltos — no se puede guardar")

        path = project.paths.project_json_path
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # Construir estructura project.json
        data = {
            "project": {
                "meta": {
                    "project_id": project.project_id.value,
                    "version": "1.0",
                    "updated_at": _now_iso(),
                },
                "identity": {
                    "name": project.name or project.project_id.value,
                    "language": project.language,
                    "framework": project.framework,
                },
                "location": {
                    "repo_path": project.repo_path,
                    "code_paths": project.code_paths,
                },
            }
        }

        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def list_all(self) -> list[Project]:
        """Lista todos los proyectos registrados."""
        from libs.path_resolver import get_projects_dir

        projects_dir = get_projects_dir()
        if not projects_dir.is_dir():
            return []

        result: list[Project] = []
        for entry in sorted(projects_dir.iterdir()):
            if not entry.is_dir():
                continue
            pid = ProjectId(entry.name)
            project = self._load_from_path(pid)
            if project:
                result.append(project)

        return result

    def exists(self, project_id: ProjectId | str) -> bool:
        """Verifica si un proyecto existe consultando project.json."""
        pid = project_id if isinstance(project_id, ProjectId) else ProjectId(project_id)
        from libs.path_resolver import get_project_dir

        proj_dir = get_project_dir(pid.value)
        manifest = proj_dir / "data" / "project.json"
        if manifest.exists():
            return True
        legacy = proj_dir / "project.json"
        return legacy.exists()


def _now_iso() -> str:
    """Timestamp ISO 8601 actual."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
