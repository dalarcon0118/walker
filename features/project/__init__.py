"""
features/project/ — Project feature (DDD + Clean Architecture).

SRP: SSOT para identidad de proyectos, perfil (project.json) y
resolucion de paths. Todo el codebase resuelve rutas via este modulo.

Arquitectura:
  domain/entities.py   → Project (aggregate root)
  domain/values.py     → ProjectId, ProjectManifest, ProjectPaths (VOs)
  domain/errors.py     → DomainError, InvalidProjectIdError, ...
  domain/events.py     → ProjectRegistered, ProjectProfileUpdated, SprintDetected
  domain/ports.py      → ProjectRepository (abstract interface)
  domain/services.py   → validate_project_id, sanitize_project_id, compute_project_paths
  application/service.py → ProjectService (thin orchestrator)
  infrastructure/       → ProjectRepository impl, FileWatcher
  context/providers.py  → ProjectContextProvider (engine SPI)

Uso:
    from features.project import ProjectService, ProjectPaths, ProjectId

    paths = ProjectService.resolve_paths(chain_dir)
    projects = ProjectService.list_available()
    active = ProjectService.find_active_sprint(project_id)
"""
from __future__ import annotations

# Domain
from .domain.entities import Project
from .domain.values import ProjectId, ProjectManifest, ProjectPaths
from .domain.errors import (
    DomainError,
    InvalidProjectIdError,
    ProjectNotFoundError,
    ProjectAlreadyExistsError,
    InvalidManifestError,
    PathResolutionError,
)
from .domain.events import ProjectRegistered, ProjectProfileUpdated, SprintDetected
from .domain.ports import ProjectRepository
from .domain.services import validate_project_id, sanitize_project_id, compute_project_paths

# Application
from .application.service import ProjectService, ProjectResolutionError, ProjectNotFoundError
from .application.use_cases import ProjectDetector, HealthReportAnalyzer

# Infrastructure (public stable facade — no import internals directly)
from .infrastructure.watcher import FileWatcher, IgnoreRules

__all__ = [
    # Domain — entities & VOs
    "Project",
    "ProjectId",
    "ProjectManifest",
    "ProjectPaths",
    # Domain — errors
    "DomainError",
    "InvalidProjectIdError",
    "ProjectNotFoundError",
    "ProjectAlreadyExistsError",
    "InvalidManifestError",
    "PathResolutionError",
    # Domain — events
    "ProjectRegistered",
    "ProjectProfileUpdated",
    "SprintDetected",
    # Domain — ports
    "ProjectRepository",
    # Domain — services
    "validate_project_id",
    "sanitize_project_id",
    "compute_project_paths",
    # Application
    "ProjectService",
    "ProjectResolutionError",
    "ProjectNotFoundError",
    "ProjectDetector",
    "HealthReportAnalyzer",
    # Infrastructure (public facade)
    "FileWatcher",
    "IgnoreRules",
]
