"""use_cases/__init__.py — SHIM de compatibilidad (ADR-WALKER-048).

FACHADA PEREZOSA (PEP 562 — ADR-WALKER-053): re-exporta bajo demanda para
romper el ciclo project_service ↔ use_cases ↔ package root detectado por el
gate DAG (ADR-WALKER-052 D1). API pública idéntica.
"""
from __future__ import annotations

from typing import Any

_EXPORTS: dict[str, str] = {
    "ProjectDetector": "features.project.application.use_cases.project_detector",
    "IGNORE_DIRS": "features.project.application.use_cases.project_detector",
    "CODE_EXTENSIONS": "features.project.application.use_cases.project_detector",
    "HealthReportAnalyzer": "features.project.application.use_cases.health_report_analyzer",
    "DetectProjectContextUseCase": "features.project.application.use_cases.detect_project_context",
    "UpdateProjectUseCase": "features.project.application.use_cases.update_project",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(target), name)


def __dir__() -> list[str]:
    return sorted(list(globals()) + list(_EXPORTS))
