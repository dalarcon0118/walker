"""
domain/models.py — Abstracciones de tareas del workflow.

Una tarea es una función pura que recibe contexto de ejecución
y datos, y retorna un resultado. El workflow la invoca por nombre.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from shared.common.shared_domain import ExecutionContext, TaskResult


class TaskHandler(ABC):
    """Abstracción de una tarea registrable.

    Cada tarea concreta implementa execute() y opcionalmente
    provee metadata para documentación y tooltips.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Nombre único con el que se registra en el TaskRegistry."""
        ...

    @property
    def metadata(self) -> dict | None:
        """Metadata opcional para el TaskRegistry."""
        return None

    @abstractmethod
    def execute(self, ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
        """Ejecuta la tarea y retorna un TaskResult."""
        ...

    def to_registrable(self) -> tuple[str, Callable, dict | None]:
        """Convierte el handler a formato registrable en TaskRegistry."""
        return (self.name, self.execute, self.metadata)
