"""
application/service.py — SHIM de compatibilidad (ADR-WALKER-048).

ProjectService ahora vive en `application/services/project_service.py`
(patrón canonical: services/ como fachada pública). Este módulo re-exporta
la clase para backward compatibility durante la migración incremental
(ADR-WALKER-048 §6): `from features.project.application.service import
ProjectService` sigue funcionando.

Consumidores nuevos: usen
`features.project.application.services.project_service.ProjectService`
(Application Service, INV-013).
"""
from __future__ import annotations

from .services.project_service import (
    ProjectService,
    ProjectResolutionError,
    ProjectNotFoundError,
    get_cognitive_dir,
    get_walker_root,
)

__all__ = [
    "ProjectService",
    "ProjectResolutionError",
    "ProjectNotFoundError",
    "get_cognitive_dir",
    "get_walker_root",
]
