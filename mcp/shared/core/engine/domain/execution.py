"""
domain/execution.py — Contrato de ejecución del workflow engine.

ProjectId... VOs del motor (contrato del puerto `ActionCatalog`):

  - `ExecutionContext` — contexto de ejecución de TODAS las operaciones del
    workflow (session/trace/span/chain/task). Lo reciben: el engine
    (declara y valida), los use cases de features (lo consumen) y las tasks
    (lo implementan vía el puerto). Por eso vive AQUÍ (dueño: el engine),
    NO en tasks/ domain — si viviera en tasks/, shared/core/engine
    importaría tasks/ = inversión prohibida (ADR-067).
  - `TaskResult` — resultado de la ejecución de una task. El engine lo
    consume (saga de side-effects), las tasks lo producen. Contrato del
    puerto `ActionCatalog` → vive con su puerto (engine/domain).

PRINCIPIO (heredero del extinto shared/common/shared_domain): VOs puros.
Sin IO, sin resolución de paths, sin infraestructura. Factories con IO
viven en los application services.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar


T = TypeVar("T")


@dataclass
class TaskResult(Generic[T]):
    """Result of a task execution. Produced by tasks; consumed by the engine."""
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
    channel: str = "workflow"  # canal de invocación: cli | mcp | workflow (SPEC-002)
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