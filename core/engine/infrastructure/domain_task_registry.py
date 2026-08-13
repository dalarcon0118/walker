"""
infrastructure/domain_task_registry.py — Vista filtrada de TaskRegistry para tasks de dominio.

SRP: Proporciona acceso SOLO a tasks internas del workflow engine.
Las tasks de dominio son invocadas por el motor de workflow, no por CLI/MCP.
"""
from __future__ import annotations

from typing import Any, Callable

from .task_registry import InMemoryTaskRegistry
from ..domain.registry import TaskRegistry
from ..domain.models import TaskResult, ExecutionContext


class DomainTaskRegistry(TaskRegistry):
    """Vista filtrada de TaskRegistry: solo tasks de dominio (scope=internal/domain)."""

    _DOMAIN_TASKS = frozenset({
        "plan_sprint", "spec_task", "review_task", "gather_design", "gather_analysis",
        "user_gate", "implement", "scan_code", "review_code", "report_gate",
        "verify_user_gate", "qa_run", "po_accept", "start_task", "close_sprint",
        "advance_task", "update_sprint_progress", "show_dashboard",
        "log_rejection_to_ckg", "notify_po_escalation", "archive_sprint_metrics",
        "prepare_analysis_context", "build_verification_context", "build_task_briefing",
    })

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
        meta.setdefault("scope", "domain")
        self._ensure_base().register(name, fn, meta)

    def execute(
        self, name: str, ctx: ExecutionContext, data: dict[str, Any] | None = None
    ) -> TaskResult:
        return self._ensure_base().execute(name, ctx, data)

    def list_tasks(self) -> list[str]:
        base = self._ensure_base()
        return [name for name in base.list_tasks() if self._is_domain_task(name, base)]

    def _is_domain_task(self, name: str, base: InMemoryTaskRegistry) -> bool:
        info = base.get_task_info(name)
        if info:
            scope = info.get("scope")
            if scope in ("internal", "domain"):
                return True
        if name in self._DOMAIN_TASKS:
            return True
        return False

    def list_tasks_with_meta(self, scope: str | None = None) -> list[dict]:
        base = self._ensure_base()
        tasks = [
            {"name": name, **base.get_task_info(name)}
            for name in base.list_tasks()
            if self._is_domain_task(name, base)
        ]
        if scope is not None:
            tasks = [t for t in tasks if t.get("scope", "domain") == scope]
        return tasks

    def get_task(self, name: str) -> Callable | None:
        if not self._is_domain_task(name, self._ensure_base()):
            return None
        return self._ensure_base().get_task(name)

    def get_task_info(self, name: str) -> dict | None:
        base = self._ensure_base()
        if not self._is_domain_task(name, base):
            return None
        return base.get_task_info(name)

    def discover(self, scope: str = "domain") -> dict:
        base = self._ensure_base()
        tasks = []
        for name in base.list_tasks():
            if not self._is_domain_task(name, base):
                continue
            info = base.get_task_info(name) or {"name": name}
            task_entry = {
                "name": name,
                "description": info.get("description", ""),
                "usage": info.get("usage", f"walker <project>/{name}"),
                "example": info.get("example", ""),
                "query_instructions": info.get("query_instructions", ""),
                "tags": info.get("tags", []),
                "scope": info.get("scope", "domain"),
            }
            if "use_cases" in info:
                task_entry["use_cases"] = info["use_cases"]
            if "input_schema" in info:
                task_entry["input_schema"] = info["input_schema"]
            tasks.append(task_entry)
        return {
            "total": len(tasks),
            "tasks": tasks,
        }
