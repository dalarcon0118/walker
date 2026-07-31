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

    def list_tasks_with_meta(self, scope: str | None = None) -> list[dict]:
        """Retorna tareas con metadata, opcionalmente filtradas por scope.

        Args:
            scope: "public" (default), "internal", o None (todas).
        """
        tasks = [
            {"name": name, **self._metadata.get(name, {})}
            for name in sorted(self._tasks.keys())
        ]
        if scope is not None:
            tasks = [t for t in tasks if t.get("scope", "public") == scope]
        return tasks

    def get_task(self, name: str) -> Callable | None:
        return self._tasks.get(name)

    def get_task_info(self, name: str) -> dict | None:
        info = self._metadata.get(name)
        if info:
            return {"name": name, **info}
        return {"name": name} if name in self._tasks else None

    def discover(self, scope: str = "public") -> dict:
        """Retorna catalogo de tareas PUBLICAS con metadata para auto-documentacion.

        Las tareas con scope "internal" (ej: plan_sprint) son del workflow:
        NO se invocan desde el CLI, solo las ejecuta el motor internamente.
        Por eso por defecto solo se listan las publicas.
        """
        tasks = []
        for name in sorted(self._tasks.keys()):
            meta = self._metadata.get(name, {})
            if meta.get("scope", "public") == "internal" and scope != "all":
                continue  # Tareas internas del workflow no se exponen al agente
            task_entry = {
                "name": name,
                "description": meta.get("description", ""),
                "usage": meta.get("usage", f"walker <project>/{name}"),
                "example": meta.get("example", ""),
                "query_instructions": meta.get("query_instructions", ""),
                "tags": meta.get("tags", []),
                "scope": meta.get("scope", "public"),
            }
            # Incluir use_cases si existen (auto-documentacion)
            if "use_cases" in meta:
                task_entry["use_cases"] = meta["use_cases"]
            tasks.append(task_entry)
        return {
            "total": len(tasks),
            "tasks": tasks,
        }
