"""domain/models.py — Re-exports para compatibilidad hacia atrás.

DEPRECATED: Este módulo es un catch-all de re-exports para compatibilidad.
Las nuevas importaciones deben ir a los módulos específicos:
    - domain/enums.py    → ExecutionType, DomainEventType
    - domain/values.py   → RetryPolicy, SideEffectDef, SelectOption, SubStateKey, SubStateData
    - domain/entities.py → SubState, WorkflowNode, WorkflowGraph, WorkflowState, NodeExecution, DomainEvent, Gate
    - domain/services.py → RequiredFieldResolver, ValidationService, AdvanceService, TransitionService
"""
from __future__ import annotations

from shared.core.engine.domain.enums import (
    ExecutionType,
    SubStateMode,
)
from shared.core.engine.domain.values import (
    RetryPolicy,
    SideEffectDef,
    SelectOption,
    SubStateKey,
    SubStateData,
)
from shared.core.engine.domain.entities import (
    SubState,
    WorkflowNode,
    WorkflowGraph,
    WorkflowState,
    NodeExecution,
    DomainEvent,
    Gate,
)

# ── Re-exports desde domain.services ─────────────────────────────────────
# SSOT: features/engine/domain/services/
# Se re-exportan aqui para compatibilidad hacia atras.
from shared.core.engine.domain.services import RequiredFieldResolver  # noqa: F401

# ── Re-exports del contrato de ejecución ─────────────────────────────────
# SSOT: shared/core/engine/domain/execution.py (TaskResult, ExecutionContext)
# Se re-exportan aqui para compatibilidad hacia atras.
# Las nuevas importaciones deben ir a shared/core/engine/domain/execution.
from shared.core.engine.domain.execution import ExecutionContext, TaskResult  # noqa: F401

__all__ = [
    "ExecutionType",
    "SubStateMode",
    "RetryPolicy",
    "SideEffectDef",
    "SelectOption",
    "SubStateKey",
    "SubStateData",
    "SubState",
    "WorkflowNode",
    "WorkflowGraph",
    "WorkflowState",
    "NodeExecution",
    "DomainEvent",
    "Gate",
    "RequiredFieldResolver",
    "ExecutionContext",
    "TaskResult",
]
