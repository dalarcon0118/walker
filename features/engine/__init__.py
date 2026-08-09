"""
engine — Workflow engine + ModuleRegistry.

API pública:
    from features.engine import (
        # Core engine
        WorkflowState,
        WorkflowGraph,
        load_workflow,
        load_state,
        save_state,
        # ModuleRegistry
        ModuleDefinition,
        ToolDefinition,
        RestRoute,
        IModuleRegistry,
        # Bootstrap
        bootstrap,
        bootstrap_modules,
        build_context_registry,
        # Task dispatching
        dispatch_public_task,
        # Domain models
        ExecutionType,
        NodeType,
        GateType,
        SubState,
        WorkflowNode,
        Gate,
        NodeExecution,
        DomainEvent,
    )

Arquitectura:
    domain/         → Modelos puros (WorkflowState, WorkflowGraph, SubState, etc.)
    application/    → Orquestadores, task_dispatcher, workflow_api
    infrastructure/ → ModuleRegistry, TaskRegistry, repositorios
    interface/      → Contratos públicos del engine
    workflow/       → Nodos y schema del workflow
    bootstrap.py    → Descubrimiento automático de módulos y tasks
"""

from __future__ import annotations

# ── Domain models ────────────────────────────────────────────────────────────
from features.engine.domain.models import (
    ExecutionType,
    NodeType,
    GateType,
    RetryPolicy,
    SideEffectDef,
    SelectOption,
    SubStateKey,
    SubStateData,
    SubState,
    WorkflowNode,
    WorkflowGraph,
    WorkflowState,
    NodeExecution,
    DomainEvent,
    Gate,
)
from features.engine.domain.enums import DomainEventType
from features.engine.domain.ports import IWorkflowIO, IWorkflowLoader, IPresenter

# ── Module registry ──────────────────────────────────────────────────────────
from features.engine.interface.contracts import (
    ToolDefinition,
    RestRoute,
    ModuleDefinition,
    IModuleRegistry,
)
from features.engine.infrastructure.module_registry import InMemoryModuleRegistry

# ── Core engine functions ────────────────────────────────────────────────────
from features.engine.engine import (
    load_workflow,
    load_state,
    save_state,
    advance_sub_state,
    is_node_complete,
    check_transition_preconditions,
    validate_gate,
)
from features.engine.application.workflow_loader import find_workflow
from features.engine.application.state_manager import ensure_state

# ── Bootstrap ────────────────────────────────────────────────────────────────
from features.engine.bootstrap import (
    bootstrap,
    bootstrap_modules,
    build_context_registry,
)

# ── Task dispatching ─────────────────────────────────────────────────────────
from features.engine.application.task_dispatcher import dispatch_public_task

__all__ = [
    # Domain models
    "ExecutionType",
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
    "Gate",
    # Ports
    "IWorkflowIO",
    "IWorkflowLoader",
    "IPresenter",
    # Module registry
    "ToolDefinition",
    "RestRoute",
    "ModuleDefinition",
    "IModuleRegistry",
    "InMemoryModuleRegistry",
    # Core engine
    "load_workflow",
    "load_state",
    "save_state",
    "find_workflow",
    "ensure_state",
    "advance_sub_state",
    "is_node_complete",
    "check_transition_preconditions",
    "validate_gate",
    # Bootstrap
    "bootstrap",
    "bootstrap_modules",
    "build_context_registry",
    # Task dispatching
    "dispatch_public_task",
]
