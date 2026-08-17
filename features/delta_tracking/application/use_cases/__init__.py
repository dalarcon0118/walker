"""use_cases/__init__.py — Delta tracking use cases (ADR-WALKER-048).

Los use cases del feature viven en `use_cases/` (un archivo por clase,
patrón canonical). Consumidores externos: usen los Application Services
(`features.delta_tracking.application.services.*`), NUNCA estos Use Cases
directos (INV-013).
"""
from __future__ import annotations

__all__: list[str] = []
