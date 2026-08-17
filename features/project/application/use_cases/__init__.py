"""use_cases/__init__.py — SHIM de compatibilidad (ADR-WALKER-048).

Los use cases de project viven en `use_cases/` (un archivo por clase,
patrón canonical). Este __init__ re-exporta las clases para backward
compatibility durante la migración incremental (ADR-WALKER-048 §6): `from
features.project.application.use_cases import X` sigue funcionando.

Consumidores nuevos: usen los Application Services
(`features.project.application.services.project_service`), NUNCA estos Use
Cases directos (INV-013).
"""
from __future__ import annotations

from .project_detector import ProjectDetector, IGNORE_DIRS, CODE_EXTENSIONS
from .health_report_analyzer import HealthReportAnalyzer
from .detect_project_context import DetectProjectContextUseCase
from .update_project import UpdateProjectUseCase

__all__ = [
    "ProjectDetector",
    "HealthReportAnalyzer",
    "DetectProjectContextUseCase",
    "UpdateProjectUseCase",
    "IGNORE_DIRS",
    "CODE_EXTENSIONS",
]
