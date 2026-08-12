"""
infrastructure/system_task_registry.py — Vista filtrada de TaskRegistry para tasks públicas.

SRP: Proporciona acceso SOLO a tasks públicas (scope=public).
Las tasks públicas son invocables desde CLI y MCP directamente.

Visibilidad SSOT (SPEC-005 #7): metadata.scope del registro. No hay listas
hardcodeadas (_PUBLIC_TASKS eliminado). InMemoryTaskRegistry.register aplica
scope=public por defecto; las tasks internas/system declaran scope explícito.
"""
from __future__ import annotations

from typing import Any, Callable

from .task_registry import InMemoryTaskRegistry
from ..domain.registry import TaskRegistry
from ..domain.models import TaskResult, ExecutionContext


class SystemTaskRegistry(TaskRegistry):
    """Vista filtrada de TaskRegistry: solo tasks públicas (scope=public)."""

    def __init__(self, base: InMemoryTaskRegistry | None = None, tracer: Any | None = None):
        self._base = base
        self._tracer = tracer

    def _ensure_base(self) -> InMemoryTaskRegistry:
        if self._base is None:
            from .task_registry import InMemoryTaskRegistry
            self._base = InMemoryTaskRegistry(tracer=self._tracer)
        return self._base

    def register(
        self, name: str, fn: Callable[..., TaskResult], metadata: dict | None = None
    ) -> None:
        meta = dict(metadata) if metadata else {}
        meta.setdefault("scope", "public")
        self._ensure_base().register(name, fn, meta)

    def execute(
        self, name: str, ctx: ExecutionContext, data: dict[str, Any] | None = None
    ) -> TaskResult:
        return self._ensure_base().execute(name, ctx, data)

    def _is_public_task(self, name: str, base: InMemoryTaskRegistry) -> bool:
        info = base.get_task_info(name)
        if info:
            scope = info.get("scope")
            if scope == "public":
                return True
            if scope == "system":
                return False
        return False

    def list_tasks(self) -> list[str]:
        base = self._ensure_base()
        return [name for name in base.list_tasks() if self._is_public_task(name, base)]

    def list_tasks_with_meta(self, scope: str | None = None) -> list[dict]:
        base = self._ensure_base()
        if scope == "system":
            tasks = [
                {"name": name, **base.get_task_info(name)}
                for name in base.list_tasks()
                if (base.get_task_info(name) or {}).get("scope") == "system"
            ]
        else:
            tasks = [
                {"name": name, **base.get_task_info(name)}
                for name in base.list_tasks()
                if self._is_public_task(name, base)
            ]
        if scope is not None and scope != "system":
            tasks = [t for t in tasks if t.get("scope", "public") == scope]
        return tasks

    def get_task(self, name: str) -> Callable | None:
        if not self._is_public_task(name, self._ensure_base()):
            return None
        return self._ensure_base().get_task(name)

    def get_task_info(self, name: str) -> dict | None:
        base = self._ensure_base()
        if not self._is_public_task(name, base):
            return None
        return base.get_task_info(name)

    def discover(self, scope: str = "public") -> dict:
        base = self._ensure_base()
        tasks = []
        for name in base.list_tasks():
            if scope == "system":
                info = base.get_task_info(name) or {}
                if info.get("scope") != "system":
                    continue
            elif not self._is_public_task(name, base):
                continue
            info = base.get_task_info(name) or {"name": name}
            task_entry = {
                "name": name,
                "description": info.get("description", ""),
                "usage": info.get("usage", f"walker <project>/{name}"),
                "example": info.get("example", ""),
                "query_instructions": info.get("query_instructions", ""),
                "tags": info.get("tags", []),
                "scope": info.get("scope", "public"),
            }
            if "use_cases" in info:
                task_entry["use_cases"] = info["use_cases"]
            if "input_schema" in info:
                task_entry["input_schema"] = info["input_schema"]
            if "output_schema" in info:
                task_entry["output_schema"] = info["output_schema"]
            tasks.append(task_entry)
        return {
            "total": len(tasks),
            "tasks": tasks,
        }
