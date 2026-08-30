"""engine/domain — Dominio del workflow engine.

Arquitectura:
    enums.py    → ExecutionType, DomainEventType
    values.py   → RetryPolicy, SideEffectDef, SelectOption, SubStateKey, SubStateData
    entities.py → SubState, WorkflowNode, WorkflowGraph, WorkflowState, NodeExecution, DomainEvent
    services.py → RequiredFieldResolver, otros servicios de dominio
    models.py   → Re-exports para compatibilidad hacia atrás
"""
from __future__ import annotations

from shared.core.engine.domain.enums import (
    ExecutionType,
    SubStateMode,
    DomainEventType,
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
)

__all__ = [
    "ExecutionType",
    "SubStateMode",
    "DomainEventType",
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
]
