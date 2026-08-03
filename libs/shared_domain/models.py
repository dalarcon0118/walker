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

    # ── Operaciones monádicas (patrón Result/Monad) ──────────────
    # Permiten componer tareas encadenadas sin if/else: map/bind propaga
    # el error automáticamente (short-circuit) y mantiene el tipo.

    def is_success(self) -> bool:
        return self.ok

    def map(self, fn: Any) -> "TaskResult":
        """bind: aplica fn al data si ok. Si failed, propaga el error (short-circuit)."""
        if not self.ok:
            return self
        try:
            return TaskResult.success(fn(self.data))
        except Exception as e:
            return TaskResult.failure(e, data=self.data)

    def and_then(self, fn: Any) -> "TaskResult":
        """chain: aplica fn al data (debe devolver otro TaskResult) si ok."""
        if not self.ok:
            return self
        try:
            next_result = fn(self.data)
            if isinstance(next_result, TaskResult):
                return next_result
            return TaskResult.success(next_result)
        except Exception as e:
            return TaskResult.failure(e, data=self.data)

    def tap(self, fn: Any) -> "TaskResult":
        """ejecuta fn con el data (side-effect) sin modificar el resultado."""
        if self.ok and self.data is not None:
            try:
                fn(self.data)
            except Exception:
                pass  # tap es observacional; no debe romper el flujo
        return self

    def recover(self, fn: Any) -> "TaskResult":
        """recupera de un error: fn(error) → data de respaldo si failed."""
        if self.ok:
            return self
        try:
            return TaskResult.success(fn(self.error or ""))
        except Exception:
            return self


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
