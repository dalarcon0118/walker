"""
bootstrap.py — Registro de tareas de task_execution en el TaskRegistry.

Uso desde la aplicación (walker):
    from features.main.task_execution.bootstrap import register_all

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
    # Por ahora no hay tareas migradas — las tareas viven en features/task_execution/tasks/
    # Cuando se migre una tarea, se registra aquí:
    #
    # from .tasks.example_task import ExampleTask
    # handler = ExampleTask()
    # name, fn, meta = handler.to_registrable()
    # registry.register(f"{namespace}/{name}", fn, meta)
    pass
