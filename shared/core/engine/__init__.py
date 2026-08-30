"""
engine — Workflow engine (componente interno de `shared`).

ESTE MÓDULO ES INTERNO. No es el paquete distribuible: lo es `shared` (su
nivel superior). `engine` no sabe quién lo consume y NO re-exporta componentes
hermanos (p.ej. `shared.libs.module_system`). La re-exportación de la API
pública —engine + contratos de módulos— la hace `shared/__init__.py` (facade,
INV-015 / ADR-WALKER-067).

NATURALEZA (para que no quede duda): `engine` es una **máquina de estados BPMN
genérica** — carga la config del workflow y la ejecuta. NO conoce conceptos de
negocio del walker: `sprint`/`spring`, `spec`, `delta`, `cpg`/`ckg`/`trace`,
`project`, sesiones del negocio. Esos viven en el HOST (features/app) o en el
dominio del walker. `ExecutionContext` (shared.common.shared_domain) ES la
entrada de la máquina (contexto de instancia BPMN: step/submit/transition lo
reciben); el concepto "sprint" NO existe aquí (SprintId fue eliminado por ser
vocabulario walker muerto — ADR-WALKER-067 §8/P1).

API pública de `engine` (lo que expone el motor en sí):
    from shared.core.engine import (
        WorkflowState, WorkflowGraph,
        load_workflow, load_state, save_state,
        find_workflow, ensure_state,
        build_context_registry,
        ExecutionType, DomainEventType,
        RetryPolicy, SideEffectDef, SelectOption,
        SubStateKey, SubStateData, SubState,
        WorkflowNode, NodeExecution, DomainEvent, Gate,
        IWorkflowIO, IWorkflowLoader, IPresenter,
    )

Arquitectura:
    domain/         → Modelos puros (WorkflowState, WorkflowGraph, SubState, etc.)
    application/    → Orquestadores, task_dispatcher, workflow_api, state_manager
    infrastructure/ → TaskRegistry, repositorios, config, side_effects
    bootstrap.py    → Descubrimiento automático de módulos y tasks
"""

from __future__ import annotations

# ── Domain models ────────────────────────────────────────────────────────────
from shared.core.engine.domain.models import (
    ExecutionType,
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
from shared.core.engine.domain.enums import DomainEventType
from shared.core.engine.domain.ports import IWorkflowIO, IWorkflowLoader, IPresenter

# ── Core engine functions ────────────────────────────────────────────────────
from shared.core.engine.engine import (
    load_workflow,
    load_state,
    save_state,
)
from shared.core.engine.application.workflow_loader import find_workflow
from shared.core.engine.application.state_manager import ensure_state

# ── Bootstrap ────────────────────────────────────────────────────────────────
from shared.core.engine.bootstrap import (
    build_context_registry,
)

__all__ = [
    # Domain models
    "ExecutionType",
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
    # Ports (del propio engine)
    "IWorkflowIO",
    "IWorkflowLoader",
    "IPresenter",
    # Core engine
    "load_workflow",
    "load_state",
    "save_state",
    "find_workflow",
    "ensure_state",
    # Bootstrap
    "build_context_registry",
]
