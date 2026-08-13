"""
domain/module.py — ModuleRegistry: interfaces abstractas del registry.

SINGULARIZADO: los contratos IModule/IModuleRegistry viven en
interface/contracts.py (contratos públicos del engine). Este módulo re-exporta
desde ahí para compatibilidad con implementaciones previas, SIN definir clases
duplicadas.

Nota: la responsabilidad de definir contratos publicos es de
features/engine/interface/contracts.py (SSOT). Eliminar importaciones
directas de core.engine.domain.module a favor de interface.contracts.
"""
from __future__ import annotations

from core.engine.interface.contracts import (
    IModule,
    IModuleRegistry,
)

__all__ = ["IModule", "IModuleRegistry"]