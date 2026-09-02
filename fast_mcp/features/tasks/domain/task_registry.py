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
    def failure(cls, error: str) -> TaskResult:
        return cls(ok=False, error=error)


@dataclass(frozen=True)
class ExecutionContext:
    """Context passed to task handlers."""
    chain_dir: str = ""
    task_id: str = ""
    session_id: str = ""
    channel: str = "mcp"

    @classmethod
    def create(
        cls,
        chain_dir: str = "",
        task_id: str = "",
        session_id: str = "mcp",
        channel: str = "mcp",
    ) -> ExecutionContext:
        return cls(chain_dir=chain_dir, task_id=task_id, session_id=session_id, channel=channel)


class TaskRegistry:
    """In-memory task registry.

    Register: task_name → handler + metadata.
    Execute: task_name → handler(ctx, data) → TaskResult.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Callable] = {}
        self._meta: dict[str, TaskMeta] = {}

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
            return TaskResult.failure(f"unknown task: {name}")
        try:
            result = handler(ctx, data)
            if isinstance(result, TaskResult):
                return result
            if isinstance(result, dict):
                return TaskResult(ok=result.get("ok", True), data=result.get("data", result), error=result.get("error", ""))
            return TaskResult(success={"result": result})
        except Exception as exc:
            logger.exception("TaskRegistry: task '%s' failed", name)
            return TaskResult.failure(f"{type(exc).__name__}: {exc}")

    def has(self, name: str) -> bool:
        return name in self._handlers

    def list_tasks(self) -> dict[str, TaskMeta]:
        return dict(self._meta)
