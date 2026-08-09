"""domain/models.py — Re-exports para compatibilidad hacia atrás.

DEPRECATED: Este módulo es un catch-all de re-exports para compatibilidad.
Las nuevas importaciones deben ir a los módulos específicos:
    - domain/enums.py    → ExecutionType, NodeType, GateType, DomainEventType
    - domain/values.py   → RetryPolicy, SideEffectDef, SelectOption, SubStateKey, SubStateData
    - domain/entities.py → SubState, WorkflowNode, WorkflowGraph, WorkflowState, NodeExecution, DomainEvent, Gate
    - domain/services.py → RequiredFieldResolver, ValidationService, AdvanceService, TransitionService
"""
from __future__ import annotations

from features.engine.domain.enums import (
    ExecutionType,
    SubStateMode,
    NodeType,
    GateType,
)
from features.engine.domain.values import (
    RetryPolicy,
    SideEffectDef,
    SelectOption,
    SubStateKey,
    SubStateData,
)
from features.engine.domain.entities import (
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
from features.engine.domain.services import RequiredFieldResolver  # noqa: F401

# ── Re-exports desde libs.shared_domain ─────────────────────────────────────
# SSOT: libs/shared_domain/models.py
# Se re-exportan aqui para compatibilidad hacia atras.
# Las nuevas importaciones deben ir a libs.shared_domain.
from libs.shared_domain.models import ExecutionContext, TaskResult  # noqa: F401

__all__ = [
    "ExecutionType",
    "SubStateMode",
    "NodeType",
    "GateType",
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
