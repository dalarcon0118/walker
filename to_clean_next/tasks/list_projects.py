"""
scripts/tasks/list_projects.py — SHIM: re-export desde features/task_execution/.

La implementación real está en features/task_execution/tasks/list_projects.py.
Este shim existe para que el engine (que descubre tasks desde scripts/tasks/)
siga encontrando la task sin cambios en el bootstrap.

Cuando todas las tasks estén migradas, este archivo se elimina y el
engine descubre directamente desde features/task_execution/tasks/.
"""

from __future__ import annotations

# Re-exportar la implementación real desde la nueva ubicación
from features.task_execution.tasks.list_projects import list_projects_handler, register  # noqa: F401
