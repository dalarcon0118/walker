"""domain/task_registry.py — TaskRegistry (in-memory, OCP-compliant).

Simple registry: register task handlers by name, execute by name.
Same pattern as handler registries in workflow/memories/event_tracing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskMeta:
    """Metadata for a registered task."""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    scope: str = "internal"  # internal | public


@dataclass
class TaskResult:
    """Result of a task execution."""
    ok: bool = True
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @classmethod
    def success(cls, data: dict[str, Any] | None = None) -> TaskResult:
        return cls(ok=True, data=data or {})

    @classmethod
    def failure(cls, error: str, data: dict[str, Any] | None = None) -> TaskResult:
        """Fallo con mensaje accionable.

        `data` es opcional y aditivo: un rojo puede traer su evidencia
        estructurada (p. ej. `output_tail` de una suite roja) sin perder el
        contrato de error. El dispatcher conserva ambos.
        """
        return cls(ok=False, data=data or {}, error=error)


@dataclass(frozen=True)
class ExecutionContext:
    """Context passed to task handlers."""
    chain_dir: str = ""
    task_id: str = ""
    session_id: str = ""
    channel: str = "mcp"
    cpg_adapter: Any = None
    project_id: str = ""

    @classmethod
    def create(
        cls,
        chain_dir: str = "",
        task_id: str = "",
        session_id: str = "mcp",
        channel: str = "mcp",
        cpg_adapter: Any = None,
        project_id: str = "",
    ) -> ExecutionContext:
        return cls(
            chain_dir=chain_dir,
            task_id=task_id,
            session_id=session_id,
            channel=channel,
            cpg_adapter=cpg_adapter,
            project_id=project_id,
        )


class TaskRegistry:
    """In-memory task registry.

    Register: task_name → handler + metadata.
    Execute: task_name → handler(ctx, data) → TaskResult.
    """

    def __init__(self, on_task_done: Callable | None = None) -> None:
        self._handlers: dict[str, Callable] = {}
        self._meta: dict[str, TaskMeta] = {}
        # Hook inyectado por composition (R1): el registro no conoce el bus de
        # hooks ni el disco; solo notifica nombre/ctx/resultado.
        self._on_task_done = on_task_done

    def register(
        self,
        name: str,
        handler: Callable,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register a task handler."""
        self._handlers[name] = handler
        self._meta[name] = TaskMeta(
            description=metadata.get("description", "") if metadata else "",
            tags=metadata.get("tags", []) if metadata else [],
            scope=metadata.get("scope", "internal") if metadata else "internal",
        )
        logger.debug("TaskRegistry: registered '%s' (%s)", name, self._meta[name].scope)

    def execute(self, name: str, ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
        """Execute a task by name. Never raises."""
        handler = self._handlers.get(name)
        if handler is None:
            result = TaskResult.failure(f"unknown task: {name}")
        else:
            try:
                raw = handler(ctx, data)
                if isinstance(raw, TaskResult):
                    result = raw
                elif isinstance(raw, dict):
                    result = TaskResult(
                        ok=raw.get("ok", True),
                        data=raw.get("data", raw),
                        error=raw.get("error", ""),
                    )
                else:
                    result = TaskResult.success({"result": raw})
            except Exception as exc:
                logger.exception("TaskRegistry: task '%s' failed", name)
                result = TaskResult.failure(f"{type(exc).__name__}: {exc}")
        self._notify(name, ctx, result)
        return result

    def _notify(self, name: str, ctx: ExecutionContext, result: TaskResult) -> None:
        """Emite `task.after` (best-effort: un hook nunca rompe la ejecución)."""
        if self._on_task_done is None:
            return
        try:
            self._on_task_done(name, ctx, result)
        except Exception:  # noqa: BLE001 — el hook es observador, no parte del task
            logger.warning("TaskRegistry: on_task_done falló para '%s'", name, exc_info=True)

    def has(self, name: str) -> bool:
        return name in self._handlers

    def list_tasks(self) -> dict[str, TaskMeta]:
        return dict(self._meta)
