"""
infrastructure/task_registry.py — Implementacion concreta de TaskRegistry.

SRP: Unica responsabilidad = almacenar y ejecutar tareas registradas.
Tambien registra eventos task_execute en el TraceGraph.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from ..domain.registry import TaskRegistry
from ..domain.models import TaskResult, ExecutionContext
from ..domain.events import WorkflowEvent
from ..domain.ports import ITracer


class InMemoryTaskRegistry(TaskRegistry):
    """TaskRegistry en memoria. Tareas registradas por nombre con metadata."""

    def __init__(self, tracer: ITracer | None = None):
        self._tasks: dict[str, Callable] = {}
        self._metadata: dict[str, dict] = {}
        self._tracer = tracer

    def register(self, name: str, fn: Callable[..., TaskResult],
                 metadata: dict | None = None, scope: str = "public") -> None:
        self._tasks[name] = fn
        if metadata:
            meta = dict(metadata)
            meta.setdefault("scope", scope)
            self._metadata[name] = meta
        else:
            self._metadata[name] = {"scope": scope}

    def execute(
        self, name: str, ctx: ExecutionContext, data: dict[str, Any] | None = None
    ) -> TaskResult:
        task = self._tasks.get(name)
        if not task:
            from ..domain.errors import TaskNotFoundError
            return TaskResult.failure(
                TaskNotFoundError(f"Task '{name}' no registrada en TaskRegistry")
            )
        started = time.time()
        try:
            result = task(ctx, data or {})
            if not isinstance(result, TaskResult):
                result = TaskResult.success(data=result)
            self._trace_execute(name, ctx, data, result, started)
            return result
        except Exception as e:
            error_result = TaskResult.failure(
                type(e)(f"Error ejecutando task '{name}': {e}") if isinstance(e, Exception) else Exception(f"Error: {e}")
            )
            self._trace_execute(name, ctx, data, error_result, started)
            return error_result

    def _trace_execute(
        self,
        name: str,
        ctx: ExecutionContext,
        data: dict[str, Any] | None,
        result: TaskResult,
        started: float,
    ) -> None:
        """Registra un evento task_execute en el TraceGraph."""
        if not self._tracer or not getattr(ctx, "chain_dir", ""):
            return
        duration_ms = int((time.time() - started) * 1000)
        payload = dict(data or {})
        # No trazar payloads gigantes (pueden contener código/base64)
        if isinstance(payload, dict):
            for k in list(payload.keys()):
                if isinstance(payload[k], str) and len(payload[k]) > 500:
                    payload[k] = payload[k][:500] + "..."
        try:
            self._tracer.trace_event(
                WorkflowEvent.of(
                    "task_execute",
                    payload={
                        "task": name,
                        "ok": result.ok,
                        "error": result.error if not result.ok else "",
                        "duration_ms": duration_ms,
                        "channel": getattr(ctx, "channel", "workflow"),
                        "data": payload,
                    },
                    task_id=getattr(ctx, "task_id", "") or name,
                    session_id=getattr(ctx, "session_id", ""),
                    chain_dir=getattr(ctx, "chain_dir", ""),
                )
            )
        except Exception:
            pass

    def list_tasks(self) -> list[str]:
        return list(self._tasks.keys())

    def list_tasks_with_meta(self, scope: str | None = None) -> list[dict]:
        """Retorna tareas con metadata, opcionalmente filtradas por scope.

        Args:
            scope: "public" (default), "internal", "domain", "system", o None (todas).
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
        """Retorna catalogo de tareas con metadata para auto-documentacion.

        Las tareas con scope "internal"/"domain" son del workflow:
        NO se exponen al agente por defecto.
        Las tareas con scope "public"/"system" SI se exponen.
        """
        tasks = []
        for name in sorted(self._tasks.keys()):
            meta = self._metadata.get(name, {})
            task_scope = meta.get("scope", "public")
            if task_scope in ("internal", "domain") and scope != "all":
                continue  # Tareas internas del workflow no se exponen al agente
            task_entry = {
                "name": name,
                "description": meta.get("description", ""),
                "usage": meta.get("usage", f"walker <project>/{name}"),
                "example": meta.get("example", ""),
                "query_instructions": meta.get("query_instructions", ""),
                "tags": meta.get("tags", []),
                "scope": task_scope,
            }
            if "use_cases" in meta:
                task_entry["use_cases"] = meta["use_cases"]
            if "input_schema" in meta:
                task_entry["input_schema"] = meta["input_schema"]
            tasks.append(task_entry)
        return {
            "total": len(tasks),
            "tasks": tasks,
        }
