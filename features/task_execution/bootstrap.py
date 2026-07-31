"""
bootstrap.py — Registro de tareas de task_execution en el TaskRegistry.

Uso desde la aplicación (walker):
    from features.engine.bootstrap import bootstrap as engine_bootstrap
    from features.task_execution import register_all

    registry, se = engine_bootstrap()
    register_all(registry)  # registra tareas de task_execution
"""
from __future__ import annotations

from typing import Any


def register_all(registry: Any, namespace: str = "task") -> None:
    """Registra todas las tareas de task_execution en el registry.

    Args:
        registry: TaskRegistry del engine.
        namespace: Prefijo opcional para los nombres registrados.
    """
    # Por ahora no hay tareas migradas — las tareas viven en scripts/tasks/
    # Cuando se migre una tarea, se registra aquí:
    #
    # from .tasks.example_task import ExampleTask
    # handler = ExampleTask()
    # name, fn, meta = handler.to_registrable()
    # registry.register(f"{namespace}/{name}", fn, meta)
    pass
