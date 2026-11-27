"""domain/execution.py — Execution context and task result.

Pure data objects for workflow execution. No IO, no resolution.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass
class TaskResult:
    """Result of a task execution. Produced by tasks; consumed by the engine."""
    ok: bool
    data: Any = None
    error: str | None = None
    side_effects: list[dict] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, data: Any, **kw: Any) -> "TaskResult":
        return cls(ok=True, data=data, **kw)

    @classmethod
    def failure(cls, error: str | Exception, data: dict | None = None) -> "TaskResult":
        msg = str(error) if isinstance(error, Exception) else error
        return cls(ok=False, error=msg, data=data)

    def is_success(self) -> bool:
        return self.ok

    def map(self, fn: Any) -> "TaskResult":
        if not self.ok:
            return self
        try:
            return TaskResult.success(fn(self.data))
        except Exception as e:
            return TaskResult.failure(e, data=self.data)

    def and_then(self, fn: Any) -> "TaskResult":
        if not self.ok:
            return self
        try:
            result = fn(self.data)
            if isinstance(result, TaskResult):
                return result
            return TaskResult.success(result)
        except Exception as e:
            return TaskResult.failure(e, data=self.data)


@dataclass
class ExecutionContext:
    """Execution context for all workflow operations. Pure data."""
    session_id: str
    trace_id: str
    span_id: str
    chain_dir: str
    task_id: str = ""
    channel: str = "mcp"
    started_at: str = ""
    cpg_adapter: Any = None
    # El project_id de la spec (CLI) es el SSOT. Antes no llegaba al contexto
    # ni a `data`, así que `session_bind`/`session_open` escribían
    # `project_id: ""` en delta (medido 2026-11-22).
    project_id: str = ""
    # SPEC-002: inyectar sprint_service via ctx en vez de _di global.
    sprint_service: Any = None

    @classmethod
    def create(cls, chain_dir: str, **kw: Any) -> "ExecutionContext":
        return cls(
            session_id=kw.pop("session_id", ""),
            trace_id=kw.pop("trace_id", uuid.uuid4().hex[:12]),
            span_id=kw.pop("span_id", uuid.uuid4().hex[:8]),
            chain_dir=chain_dir,
            **kw,
        )
