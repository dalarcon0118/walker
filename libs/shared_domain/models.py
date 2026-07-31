"""
libs/shared_domain/models.py — Pure domain value objects.

PRINCIPIO: VOs are pure data. No IO, no path resolution, no infrastructure.
           Factory methods that need IO belong in application services.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar


T = TypeVar("T")


@dataclass
class TaskResult(Generic[T]):
    """Result of a task execution. Used by ALL registered tasks."""
    ok: bool
    data: T | None = None
    error: str | None = None
    side_effects: list[dict] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""

    @classmethod
    def success(cls, data: T, **kw: Any) -> "TaskResult[T]":
        return cls(ok=True, data=data, **kw)

    @classmethod
    def failure(cls, error: str | Exception, data: dict | None = None) -> "TaskResult[T]":
        msg = str(error) if isinstance(error, Exception) else error
        return cls(ok=False, error=msg, data=data)


@dataclass
class ExecutionContext:
    """Execution context for all workflow operations.
    
    Pure data. No IO. Factory via create() for convenience.
    """
    session_id: str
    trace_id: str
    span_id: str
    chain_dir: str
    task_id: str = ""
    workflow_version: str = "5.0"
    baggage: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    previous_node: str = ""
    previous_sub_state: str = ""

    @classmethod
    def create(cls, session_id: str, chain_dir: str, **kw: Any) -> "ExecutionContext":
        return cls(
            session_id=session_id,
            trace_id=kw.pop("trace_id", uuid.uuid4().hex[:12]),
            span_id=kw.pop("span_id", uuid.uuid4().hex[:8]),
            chain_dir=chain_dir,
            **kw,
        )


@dataclass(frozen=True)
class ProjectId:
    """Value Object — project identifier. Pure data, no IO."""
    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class SprintId:
    """Value Object — sprint/chain identifier. Pure data, no IO."""
    value: str

    def __str__(self) -> str:
        return self.value
