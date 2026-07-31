"""
infrastructure/task_registry.py — Implementacion concreta de TaskRegistry.

SRP: Unica responsabilidad = almacenar y ejecutar tareas registradas.
"""
from __future__ import annotations

from typing import Any, Callable

from ..domain.registry import TaskRegistry
from ..domain.models import TaskResult, ExecutionContext


class InMemoryTaskRegistry(TaskRegistry):
    """TaskRegistry en memoria. Tareas registradas por nombre con metadata."""

    def __init__(self):
        self._tasks: dict[str, Callable] = {}
        self._metadata: dict[str, dict] = {}

    def register(self, name: str, fn: Callable[..., TaskResult],
                 metadata: dict | None = None) -> None:
        self._tasks[name] = fn
        if metadata:
            self._metadata[name] = metadata

    def execute(
        self, name: str, ctx: ExecutionContext, data: dict[str, Any] | None = None
    ) -> TaskResult:
        task = self._tasks.get(name)
        if not task:
            from ..domain.errors import TaskNotFoundError
            return TaskResult.failure(
                TaskNotFoundError(f"Task '{name}' no registrada en TaskRegistry")
            )
        try:
            result = task(ctx, data or {})
            if not isinstance(result, TaskResult):
                return TaskResult.success(data=result)
            return result
        except Exception as e:
            return TaskResult.failure(
                type(e)(f"Error ejecutando task '{name}': {e}") if isinstance(e, Exception) else Exception(f"Error: {e}")
            )

    def list_tasks(self) -> list[str]:
        return list(self._tasks.keys())

    def list_tasks_with_meta(self) -> list[dict]:
        return [
            {"name": name, **self._metadata.get(name, {})}
            for name in sorted(self._tasks.keys())
        ]

    def get_task(self, name: str) -> Callable | None:
        return self._tasks.get(name)

    def get_task_info(self, name: str) -> dict | None:
        info = self._metadata.get(name)
        if info:
            return {"name": name, **info}
        return {"name": name} if name in self._tasks else None
