"""use_cases/__init__.py — SHIM de compatibilidad (ADR-WALKER-048).

Los use cases de code_analysis viven en `use_cases/` (un archivo por clase,
patrón canonical). Este __init__ re-exporta las clases para backward
compatibility durante la migración incremental (ADR-WALKER-048 §6): `from
features.code_analysis.application.use_cases import X` sigue funcionando.

Consumidores nuevos: usen los Application Services
(`features.code_analysis.application.services.*`), NUNCA estos Use Cases
directos (INV-013).
"""
from __future__ import annotations

from .check_cpg_health import CheckCpgHealthUseCase, _read_cpg_file
from .gather_cpg_context import GatherCpgContextUseCase
from .detect_unused_functions import DetectUnusedFunctionsUseCase
from .review_code import ReviewCodeUseCase
from .project_metrics import ProjectMetricsUseCase

__all__ = [
    "CheckCpgHealthUseCase",
    "GatherCpgContextUseCase",
    "DetectUnusedFunctionsUseCase",
    "ReviewCodeUseCase",
    "ProjectMetricsUseCase",
    "_read_cpg_file",
]
