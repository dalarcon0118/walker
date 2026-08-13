"""engine/domain — Dominio del workflow engine.

Arquitectura:
    enums.py    → ExecutionType, NodeType, GateType, DomainEventType
    values.py   → RetryPolicy, SideEffectDef, SelectOption, SubStateKey, SubStateData
    entities.py → SubState, WorkflowNode, WorkflowGraph, WorkflowState, NodeExecution, DomainEvent
    services.py → RequiredFieldResolver, otros servicios de dominio
    models.py   → Re-exports para compatibilidad hacia atrás
"""
from __future__ import annotations

from core.engine.domain.enums import (
    ExecutionType,
    SubStateMode,
    NodeType,
    GateType,
    DomainEventType,
)
from core.engine.domain.values import (
    RetryPolicy,
    SideEffectDef,
    SelectOption,
    SubStateKey,
    SubStateData,
)
from core.engine.domain.entities import (
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
    "NodeType",
    "GateType",
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
