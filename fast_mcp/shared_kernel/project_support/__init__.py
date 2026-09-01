"""shared_kernel.project_support — Soporte de proyecto compartido por features.

Contiene las piezas GENÉRICAS que varios features consumen (R1: features no se
importan entre sí — estas utilidades viven en el kernel):
  - ProjectService/ProjectPaths/ProjectResolutionError (resolución de rutas)
  - FileWatcher / IgnoreRules (escaneo de cambios, .walkerignore)

Port fiel desde features/project (application/services/project_service.py +
infrastructure/watcher.py). Los métodos que dependen de delta_tracking
(find_active_sprint) conservan la defensiva del legacy: si el context delta
aún no está portado (Fase 3), devuelven None como en cualquier error.
"""

from __future__ import annotations

from fast_mcp.shared_kernel.project_support.project_service import (
    ProjectNotFoundError,
    ProjectPaths,
    ProjectResolutionError,
    ProjectService,
)

__all__ = [
    "ProjectService",
    "ProjectPaths",
    "ProjectResolutionError",
    "ProjectNotFoundError",
    "FileWatcher",
    "IgnoreRules",
]

from fast_mcp.shared_kernel.project_support.watcher import FileWatcher, IgnoreRules  # noqa: E402
