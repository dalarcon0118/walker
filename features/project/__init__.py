"""
features/project/ — Project module (DDD+Entities).

SSOT para todo lo relacionado a proyectos del walker.
TODA resolucion de paths DEBE pasar por este modulo.

Uso tipico:
    # Obtener paths de un sprint activo
    from features.project import ProjectService, ProjectPaths

    project = ProjectService.from_chain_dir(chain_dir)
    ckg_path = project.paths.ckg_path          # sin IO
    state_path = project.paths.workflow_state_path  # sin IO

    # Solo paths (sin cargar perfil del proyecto)
    paths = ProjectService.resolve_paths(chain_dir)
    data_dir = paths.data_dir

    # Descubrir proyectos
    projects = ProjectService.list_available()
    cognitive = ProjectService.find_cognitive_root()

    # Crear proyecto
    project = ProjectService.create_project(
        project_id="my-project",
        repo_path="/path/to/repo",
        language="python",
        framework="fastapi",
    )

Arquitectura:
    domain/models.py      → ProjectId, SprintId, ProjectPaths, Project (VOs + Entity)
    domain/services.py    → validacion, derivacion de paths (puro, sin IO)
    application/service.py → ProjectService (orquesta resolucion + carga)
    infrastructure/       → CognitiveProjectRepository (IO real)
"""
from __future__ import annotations

from .domain.models import ProjectId, SprintId, ProjectPaths, Project
from .domain.services import validate_project_id, sanitize_project_id
from .application.service import ProjectService, ProjectResolutionError, ProjectNotFoundError
from .infrastructure.repositories import CognitiveProjectRepository

__all__ = [
    # Domain
    "ProjectId",
    "SprintId",
    "ProjectPaths",
    "Project",
    # Domain services
    "validate_project_id",
    "sanitize_project_id",
    # Application
    "ProjectService",
    "ProjectResolutionError",
    "ProjectNotFoundError",
    # Infrastructure
    "CognitiveProjectRepository",
]
