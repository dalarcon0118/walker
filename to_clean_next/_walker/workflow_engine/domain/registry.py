"""
domain/registry.py — TaskRegistry interfaz y contrato de tareas puras.

SRP: Define el contrato que toda tarea registrable debe cumplir.
SSOT: Unico lugar donde se define como registrar y ejecutar tareas.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from .models import TaskResult, ExecutionContext


# ── Task Registry Interface ─────────────────────────────────────────────


class TaskRegistry(ABC):
    """Registry de tareas puras registrables por nombre.

    Una tarea pura recibe ExecutionContext + dict de entrada
    y retorna TaskResult.
    """

    @abstractmethod
    def register(
        self, name: str, fn: Callable[..., TaskResult], metadata: dict | None = None
    ) -> None:
        """Registra una tarea con un nombre y metadata opcional.

        metadata puede incluir:
          - description: texto libre de que hace la tarea
          - usage: como invocarla desde CLI
          - example: ejemplo de uso
          - query_instructions: como interpretar y consumir los resultados
          - tags: lista de categorias
        """

    @abstractmethod
    def execute(
        self, name: str, ctx: ExecutionContext, data: dict[str, Any] | None = None
    ) -> TaskResult:
        """Ejecuta una tarea registrada por nombre."""

    @abstractmethod
    def list_tasks(self) -> list[str]:
        """Retorna los nombres de todas las tareas registradas."""

    @abstractmethod
    def list_tasks_with_meta(self) -> list[dict]:
        """Retorna todas las tareas con su metadata."""

    @abstractmethod
    def get_task(self, name: str) -> Callable | None:
        """Retorna la funcion de una tarea o None si no existe."""

    @abstractmethod
    def get_task_info(self, name: str) -> dict | None:
        """Retorna la metadata de una tarea o None si no existe."""
