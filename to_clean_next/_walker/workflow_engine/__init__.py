"""
workflow_engine — DDD Package Facade.

SRP: Punto de entrada unico al modulo.
SSOT: Toda la logica de dominio del workflow esta en este package.

Uso:
    from _walker.workflow_engine import (
        # Domain Models
        WorkflowGraph, WorkflowNode, SprintState, SubState, Gate,
        SubStateKey, SubStateData, DomainEvent, DomainEventType,
        NodeType, GateType, SubStateMode,
        # v3 enterprise
        TaskResult, ExecutionContext,
        # Errors
        WorkflowEngineError, TransitionBlockedError, GateValidationError, ValidationError,
        AutoAdvanceError, NodeNotFoundError, SubStateNotFoundError,
        # Domain Services (puros, sin IO)
        AdvanceService, ValidationService, TransitionService,
        # Use Cases (orquestan servicios + repos)
        SubmitDataUseCase, TransitionUseCase, ValidateCurrentStateUseCase,
        ReplayUseCase,
        # Orchestration (entry point unificado)
        OrchestrationService,
        # Persistence
        PersistenceService, SnapshotMetadata,
        # Event Sourcing
        EventStore, EventRecord,
        # Infrastructure (IO)
        WorkflowRepository, SprintStateRepository, ScriptRunner,
    )
"""

# ── Domain Models ──────────────────────────────────────────────────────────
from .domain.models import (
    WorkflowGraph, WorkflowNode, SprintState, SubState, Gate,
    SubStateKey, SubStateData, DomainEvent, DomainEventType,
    NodeType, GateType, SubStateMode,
    # v3 types
    ExecutionType, RetryPolicy, SideEffectDef, SelectOption,
    # v3 enterprise
    TaskResult, ExecutionContext,
)

# ── Domain Errors ──────────────────────────────────────────────────────────
from .domain.registry import TaskRegistry
from .infrastructure.task_registry import InMemoryTaskRegistry
from .infrastructure.catalog import (
    register_catalog,
    get_task_schema,
    list_catalog_tasks,
)
from .domain.errors import (
    WorkflowEngineError,
    TransitionBlockedError, GateValidationError, ValidationError,
    AutoAdvanceError, NodeNotFoundError, SubStateNotFoundError,
    GateNotFoundError, TaskNotFoundError,
)

# ── Domain Services ────────────────────────────────────────────────────────
from .domain.services import (
    AdvanceService, ValidationService, TransitionService,
)

# ── Application Use Cases ──────────────────────────────────────────────────
from .application.use_cases import (
    SubmitDataUseCase, TransitionUseCase, ValidateCurrentStateUseCase,
    ReplayUseCase,
)

# ── Orchestration (entry point unificado) ───────────────────────────────────
from .application.orchestrator import OrchestrationService

# ── Scheduler ─────────────────────────────────────────────────────────────
from ..scheduler import (
    DAGBuilder, JobScheduler, ParallelExecutor,
    DAG, TaskNode, ExecutionResult, ScheduleReport,
    ScriptTaskRunner,
)

# ── Contracts ──────────────────────────────────────────────────────────────
from ..contracts import (
    ContractValidator, Contract, ContractReport, ContractViolation,
    CONTRACT_SCHEMA,
)

# ── Persistence ────────────────────────────────────────────────────────────
from ..persistence import (
    PersistenceService, SnapshotMetadata,
)

# ── Event Sourcing ─────────────────────────────────────────────────────────
from ..event_store import (
    EventStore, EventRecord,
)

# ── Infrastructure Repositories ────────────────────────────────────────────
from .infrastructure.repositories import (
    WorkflowRepository, SprintStateRepository, ScriptRunner,
)

# ── Trace Logger ────────────────────────────────────────────────────────────
from .infrastructure.trace_log import TraceLogger

# ── SideEffect Engine ───────────────────────────────────────────────────────
from .infrastructure.side_effects import (
    SideEffectEngine,
)
