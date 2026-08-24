"""features/project/ — Project feature (DDD + Clean Architecture).

FACHADA PEREZOSA (PEP 562 — ADR-WALKER-053): este init NO importa nada
eager — rompe el ciclo root ↔ use_cases/update_project detectado por el
gate DAG (ADR-WALKER-052 D1). La API pública es idéntica; la definición
vive en sus submódulos SSOT.

Uso (sin cambios):
    from features.project import ProjectService, ProjectPaths, ProjectId
"""
from __future__ import annotations

from typing import Any

_EXPORTS: dict[str, str] = {
    "Project": "features.project.domain.entities",
    "ProjectId": "features.project.domain.values",
    "ProjectManifest": "features.project.domain.values",
    "ProjectPaths": "features.project.domain.values",
    "DomainError": "features.project.domain.errors",
    "InvalidProjectIdError": "features.project.domain.errors",
    "ProjectNotFoundError": "features.project.domain.errors",
    "ProjectAlreadyExistsError": "features.project.domain.errors",
    "InvalidManifestError": "features.project.domain.errors",
    "PathResolutionError": "features.project.domain.errors",
    "ProjectRegistered": "features.project.domain.events",
    "ProjectProfileUpdated": "features.project.domain.events",
    "SprintDetected": "features.project.domain.events",
    "ProjectRepository": "features.project.domain.ports",
    "validate_project_id": "features.project.domain.services",
    "sanitize_project_id": "features.project.domain.services",
    "compute_project_paths": "features.project.domain.services",
    "ProjectService": "features.project.application.service",
    "ProjectResolutionError": "features.project.application.service",
    "ProjectNotFoundError": "features.project.application.service",
    "ProjectDetector": "features.project.application.use_cases",
    "HealthReportAnalyzer": "features.project.application.use_cases",
    "FileWatcher": "features.project.infrastructure.watcher",
    "IgnoreRules": "features.project.infrastructure.watcher",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'features.project' has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(target), name)


def __dir__() -> list[str]:
    return sorted(list(globals()) + list(_EXPORTS))
