"""
domain/models.py — SSOT del dominio Workflow Navigation.

Modelos puros (dataclasses) sin IO ni dependencias externas.
Toda la lógica de negocio del workflow engine se deriva de estos tipos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, TypeVar


# ── Enums (SSOT de tipos) ──────────────────────────────────────────────────

class ExecutionType(str, Enum):
    """
    Tipo de ejecucion de un sub-estado (v3).
    
    5 tipos determinan QUIEN ejecuta el paso:
    - bootstrap:  Engine inits session (TaskRegistry action)
    - auto:       TaskRegistry runs action (scripted)
    - llm_task:   LLM receives prompt (submit with schema)
    - user_input: User chooses option (submit with options)
    - manual:     Agent does external work (no automation)
    """
    BOOTSTRAP = "bootstrap"
    AUTO = "auto"
    LLM_TASK = "llm_task"
    USER_INPUT = "user_input"
    MANUAL = "manual"


# Alias para compatibilidad hacia atras
SubStateMode = ExecutionType


class NodeType(str, Enum):
    """Tipo de nodo en el grafo de workflow."""
    START = "start"
    SPRINT = "sprint"
    ANALYZE = "analyze"
    IMPLEMENT = "implement"
    VERIFY = "verify"
    REFINE = "refine"
    SPRINT_PLANNING = "sprint_planning"


class GateType(str, Enum):
    """Tipo de gate: inter-nodo (transicion) o intra-nodo (sub-state)."""
    INTER_NODE = "inter_node"
    INTRA_NODE = "intra_node"


# ── Value Objects (v3) ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class RetryPolicy:
    """Politica de reintento para sub-estados auto (TASK-009).

    Attributes:
        max_attempts: Maximos reintentos antes de on_failure.
        backoff: Algoritmo de backoff (fixed|linear|exponential|jitter).
        initial_delay_ms: Delay inicial en ms.
        on_error: Mapa de ErrorType -> ErrorStrategy.
            Ej: {"validation": "block+message", "execution": "retry"}
            Si no hay entrada para un tipo, se usa DEFAULT_STRATEGIES.
    """
    max_attempts: int = 3
    backoff: str = "exponential"  # fixed | linear | exponential | jitter
    initial_delay_ms: int = 1000
    on_error: dict[str, str] | None = None
    """
    Mapa de ErrorType -> ErrorStrategy.
    Ej: {"execution": "retry", "validation": "block+message"}
    """


@dataclass(frozen=True)
class SideEffectDef:
    """Efecto secundario declarativo a ejecutar tras un sub-estado.

    Attributes:
        type: Tipo de efecto (update_cpg | save_ckg | update_delta_graph |
              emit_event | persist_snapshot | run_script).
        params: Parametros del efecto (dependen del tipo).
        optional: Si True, fallo no bloquea el flujo ni gatilla compensacion.
        compensation: Tipo de compensacion para este efecto.
            Vacio = no tiene compensacion (solo warning log).
        idempotency_key: Key unica para evitar doble ejecucion.
            Si se re-ejecuta con misma key, retorna resultado previo.
    """
    type: str  # update_cpg | save_ckg | update_delta_graph | emit_event | persist_snapshot | run_script
    params: dict[str, object] = field(default_factory=dict)
    optional: bool = False
    compensation: str = ""  # delete_ckg_node | restore_cpg_snapshot | rollback_ckg | restore_delta_graph
    idempotency_key: str = ""  # Para evitar doble ejecucion


@dataclass(frozen=True)
class SelectOption:
    """Opcion seleccionable para sub-estados user_input."""
    value: str
    label: str = ""
    description: str = ""
    requires: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SubStateKey:
    """Identificador de un sub-estato dentro de un nodo. Inmutable."""
    node_id: str
    sub_key: str

    def __str__(self) -> str:
        return f"{self.node_id}.{self.sub_key}"


@dataclass(frozen=True)
class Gate:
    """Puerta de transicion entre nodos o sub-estados.
    
    Una gate es el unico mecanismo para cambiar de estado en el workflow.
    
    Attributes:
        transform: Transform data pipeline (TASK-007).
            Si presente, OrchestrationService ejecuta transform.action
            via TaskRegistry antes de aplicar la transicion. Los datos
            se seleccionan de sub_state_data segun transform.input y
            se almacenan en el contexto segun transform.output.
    """
    name: str
    source_node: str
    target_node: str
    target_sub_state: str | None = None
    description: str = ""
    gate_validation_script: str | None = None
    gate_valid_exit_codes: list[int] = field(default_factory=lambda: [0])
    gate_error_template: str = "Gate validation failed: $stderr"
    transform: dict | None = None
    """
    Ej: {"action": "build_task_briefing", "input": [...], "output": "..."}
    """
    context_updates: list[dict] | None = None
    """Promueve datos de sub_state_data al contexto global del workflow.
    
    Cada entrada: {"from": "NODE.SUB.field", "to": "context.field_name"}
    Se ejecuta DESPUES de la transicion, copiando el valor del sub_state_data
    al state.context para que esté disponible en los nodos siguientes.
    Ej: {"from": "SELECT.fields.project_id", "to": "context.project_id"}
    """


# ── Entity: SubState (v3) ──────────────────────────────────────────────────

@dataclass
class SubState:
    """Paso dentro de un nodo del workflow (v3).
    
    execution es el SSOT de quien ejecuta el paso.
    mode es una property de compatibilidad que delega en execution.
    """
    key: str
    label: str = ""
    execution: ExecutionType = ExecutionType.MANUAL

    # ── v3 fields ────────────────────────────────────────────────────────
    action: str = ""  # TaskRegistry action name for auto/bootstrap
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    side_effects: list[SideEffectDef] = field(default_factory=list)
    on_success: str = ""   # next sub-state key on success
    on_failure: str = ""   # next sub-state key on failure
    retry: RetryPolicy | None = None
    timeout_ms: int = 120000

    # ── Legacy (kept for backward compat) ─────────────────────────────────
    required_fields: list[str] = field(default_factory=list)
    auto_complete_script: str = ""
    on_failure_loop: bool = False
    order: int = 0

    # ── Context provides/needs (STATE_MANAGEMENT_3_LAYERS) ────────────
    provides: list[str] = field(default_factory=list)
    """Contexto que este sub-estado produce. Se promueve al global state
    al completar el nodo. Ej: ["project_id", "task_id"]"""
    needs: list[str] = field(default_factory=list)
    """Contexto que este sub-estado necesita. El ContextResolver valida en
    carga que exista un provider upstream. Ej: ["project_id", "sprint_id"]"""

    # ── v3 extras ────────────────────────────────────────────────────────
    prompt: str = ""  # Prompt for llm_task execution
    options: list[SelectOption] = field(default_factory=list)
    on_entry_message: str = ""
    prompt_id: str = ""  # Reference to prompts/{prompt_id}.prompt.md (replaces on_entry_message)
    context: dict | None = None  # Context Composer config: {sources: [...], focus: {...}}

    def __post_init__(self) -> None:
        """Normalize string to enum if needed."""
        if isinstance(self.execution, str):
            self.execution = ExecutionType(self.execution)

    @property
    def mode(self) -> SubStateMode:
        """Backward compat: mode delega en execution."""
        return self.execution

    @mode.setter
    def mode(self, value: SubStateMode) -> None:
        self.execution = value if isinstance(value, ExecutionType) else ExecutionType(value)

    def is_auto(self) -> bool:
        return self.execution in (ExecutionType.AUTO, ExecutionType.BOOTSTRAP)

    def needs_input(self) -> bool:
        return self.execution in (
            ExecutionType.LLM_TASK, ExecutionType.USER_INPUT, ExecutionType.MANUAL
        ) and len(self.required_fields) > 0


# ── Entity: WorkflowNode ───────────────────────────────────────────────────

@dataclass
class WorkflowNode:
    """Nodo del grafo de workflow. Contiene sub-estados y configuracion."""
    node_id: str
    sub_states: list[SubState] = field(default_factory=list)
    gates: list[Gate] = field(default_factory=list)
    label: str = ""
    description: str = ""
    entry_action_script: str = ""
    on_entry_message: str = ""
    provides: list[str] = field(default_factory=list)
    """Contexto que este nodo produce. Se promueve automáticamente al
    global state al transicionar. Todos los sub-estados lo heredan.
    Ej: ["session_id", "project_id", "sprint_id"]"""

    def find_sub_state(self, key: str) -> SubState | None:
        for ss in self.sub_states:
            if ss.key == key:
                return ss
        return None

    def sub_state_index(self, key: str) -> int:
        for i, ss in enumerate(self.sub_states):
            if ss.key == key:
                return i
        return -1

    @property
    def first_sub_state(self) -> SubState | None:
        return self.sub_states[0] if self.sub_states else None

    def is_complete(self, current_index: int) -> bool:
        return current_index >= len(self.sub_states)


# ── Entity: WorkflowGraph ──────────────────────────────────────────────────

@dataclass
class WorkflowGraph:
    """Grafo completo de workflow. SSOT para la definicion de estados.
    
    Contiene todos los nodos, las gates que los conectan, y los
    manejadores de eventos on:{} (TASK-006).
    No tiene estado mutable — es un grafo de definicion.
    """
    nodes: dict[str, WorkflowNode] = field(default_factory=dict)
    initial_state: str = ""
    """Nodo inicial del workflow (leido de workflow.json). El engine lo usa
    para inicializar SprintState cuando esta vacio (modo bootstrap)."""
    on_events: dict[str, dict] = field(default_factory=dict)
    """Eventos declarativos on:{}. Cada entrada:
       key = nombre del evento (ej: "task_approved")
       value = {"run": "action_name"} o {"transition": "gate_name"}
    """

    def get_node(self, node_id: str) -> WorkflowNode | None:
        return self.nodes.get(node_id)

    def find_gate(self, source: str, gate_name: str) -> Gate | None:
        node = self.nodes.get(source)
        if not node:
            return None
        for g in node.gates:
            if g.name == gate_name:
                return g
        return None

    def find_global_gate(self, source: str, gate_name: str) -> Gate | None:
        """Busca gate tanto en nodo como en gates globales."""
        return self.find_gate(source, gate_name)

    def find_event_handler(self, event_name: str) -> dict | None:
        """Busca handler para un evento on:{}.

        Returns:
            dict con "run" o "transition", o None si no hay handler.
        """
        return self.on_events.get(event_name)

    def generate_flows(self) -> list[dict]:
        """Genera narrativas de flujo auto-descubiertas desde el grafo.

        Recorre el grafo desde initial_state siguiendo las gates,
        produciendo paths legibles como:
        "START.INIT → [init_project] → SPRINT.PLANNING → ..."

        Returns:
            list[dict]: Cada entrada con nombre, descripción y steps.
        """
        if not self.nodes or not self.initial_state:
            return []

        flows: list[dict] = []
        visited: set[str] = set()

        def _follow(gate: Gate) -> list[dict]:
            """Sigue una gate hacia su nodo destino y construye steps."""
            target = gate.target_node
            if not target or target in visited:
                return []
            target_node = self.nodes.get(target)
            if not target_node:
                return []

            # Marcar como visitado para evitar ciclos
            visited.add(target)

            steps: list[dict] = []

            # Gate step
            target_sub = gate.target_sub_state or (
                target_node.first_sub_state.key if target_node.first_sub_state else ""
            )
            steps.append({
                "action": "transition",
                "gate": gate.name,
                "from": f"{gate.source_node}",
                "to": f"{target}.{target_sub}",
                "description": gate.description or f"Transition via {gate.name}",
            })

            # Walk sub-states of target node
            for ss in target_node.sub_states:
                exec_type = ss.execution.value if ss.execution else "manual"
                required = list(ss.required_fields)
                steps.append({
                    "action": "process",
                    "node": target,
                    "sub_state": ss.key,
                    "execution": exec_type,
                    "label": ss.label or ss.key,
                    "required_fields": required,
                })

            # Follow outgoing gates from target node
            for next_gate in target_node.gates:
                if next_gate.target_node and next_gate.target_node not in visited:
                    child = _follow(next_gate)
                    steps.extend(child)

            return steps

        # ── Start from initial_state ───────────────────────────────────
        start_node = self.nodes.get(self.initial_state)
        if not start_node:
            return flows

        visited.add(self.initial_state)

        # Steps for initial node sub-states
        init_steps: list[dict] = []
        for ss in start_node.sub_states:
            exec_type = ss.execution.value if ss.execution else "manual"
            init_steps.append({
                "action": "process",
                "node": self.initial_state,
                "sub_state": ss.key,
                "execution": exec_type,
                "label": ss.label or ss.key,
                "required_fields": list(ss.required_fields),
            })

        # Follow each outgoing gate from initial node as a separate flow
        for gate in start_node.gates:
            visited.clear()
            visited.add(self.initial_state)

            flow_steps = list(init_steps)
            flow_steps.extend(_follow(gate))

            # Skip flows that couldn't follow the gate (invalid target, cycle, etc.)
            if len(flow_steps) <= len(init_steps):
                continue

            # Build a compact flow string for the name
            labels = []
            for s in flow_steps:
                if s["action"] == "process":
                    labels.append(f"{s['node']}.{s['sub_state']}")
                elif s["action"] == "transition":
                    labels.append(f"[{s['gate']}]")

            flows.append({
                "name": " → ".join(labels),
                "steps": flow_steps,
                "node_count": len(set(s.get("node", "") for s in flow_steps if s.get("node"))),
            })

        # Handle case where initial node has no gates (isolated node)
        if not start_node.gates and init_steps:
            flows.append({
                "name": " → ".join(
                    f"{self.initial_state}.{s['sub_state']}" for s in init_steps
                ),
                "steps": init_steps,
                "node_count": 1,
            })

        return flows


# ── Value Object: SubStateData ─────────────────────────────────────────────

@dataclass
class SubStateData:
    """Datos capturados en un sub-estado particular."""
    fields: dict[str, Any] = field(default_factory=dict)
    completed_at: str | None = None

    def is_field_complete(self, field: str) -> bool:
        val = self.fields.get(field)
        return val is not None and val != ""

    def all_required_complete(self, required: list[str]) -> bool:
        return all(self.is_field_complete(f) for f in required)


# ── Entity: SprintState ────────────────────────────────────────────────────

@dataclass
class SprintState:
    """Estado actual del sprint dentro del workflow.
    
    Esta es la entidad RAÍZ del contexto Workflow Navigation.
    Representa DONDE estamos en el grafo y QUÉ datos hemos capturado.
    Solo se modifica a traves de TransitionUseCase o SubmitUseCase.
    """
    current_node: str = ""
    current_sub_state: str = ""
    sub_state_index: int = 0
    sub_state_data: dict[str, SubStateData] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    version: str = "5.0"
    updated_at: str = ""

    # ELM/Saga Log: ejecuciones de cada sub-estado (orden cronologico)
    node_executions: list[NodeExecution] = field(default_factory=list)

    # Datos de sprint (bridge con SpringContext)
    sprint: dict[str, Any] = field(default_factory=dict)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    task_order: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    walker: dict[str, Any] = field(default_factory=dict)
    audit: dict[str, Any] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=dict)

    def current_key(self) -> SubStateKey:
        return SubStateKey(self.current_node, self.current_sub_state)

    def get_sub_state_data(self, key: str | None = None) -> SubStateData:
        k = key or self.current_sub_state
        if k not in self.sub_state_data:
            if self.current_node and "." not in k:
                prefixed = f"{self.current_node}.{k}"
                if prefixed in self.sub_state_data:
                    return self.sub_state_data[prefixed]
            self.sub_state_data[k] = SubStateData()
        return self.sub_state_data[k]

    def record_submit(self, sub_key: str, fields: dict[str, Any]) -> None:
        data = self.get_sub_state_data(sub_key)
        data.fields.update(fields)
        data.completed_at = _now()

    def record_node_execution(
        self,
        node: str = "",
        sub_state: str = "",
        execution_type: str = "",
        action: str = "",
        submitted_fields: dict | None = None,
        task_ok: bool = True,
        task_result: Any = None,
        side_effects: list[dict] | None = None,
        error: str | None = None,
        error_type: str | None = None,
        gate: str = "",
        duration_ms: int = 0,
    ) -> None:
        """Registra una ejecucion de sub-estado en el log ELM/Saga.

        Cada llamada agrega una entrada al final de node_executions.
        El array completo permite reconstruir el estado reproduciendo
        eventos en orden cronologico.
        """
        self.node_executions.append(NodeExecution(
            node=node or self.current_node,
            sub_state=sub_state or self.current_sub_state,
            execution_type=execution_type,
            action=action,
            submitted_fields=submitted_fields or {},
            task_ok=task_ok,
            task_result=task_result,
            side_effects=side_effects or [],
            error=error,
            error_type=error_type,
            gate=gate,
            completed_at=_now(),
            duration_ms=duration_ms,
        ))
        self.updated_at = _now()

    def add_history(self, entry: str) -> None:
        self.history.append(entry)
        self.updated_at = _now()

    def to_dict(self) -> dict[str, Any]:
        """Serializa a dict para persistencia."""
        ssd = {}
        for k, v in self.sub_state_data.items():
            ssd[k] = {"fields": dict(v.fields), "completed_at": v.completed_at}
        return {
            "version": self.version,
            "current_node": self.current_node,
            "current_sub_state": self.current_sub_state,
            "sub_state_index": self.sub_state_index,
            "sub_state_data": ssd,
            "node_executions": [ne.__dict__ for ne in self.node_executions],
            "history": list(self.history),
            "context": dict(self.context),
            "updated_at": self.updated_at or _now(),
            "sprint": dict(self.sprint),
            "tasks": list(self.tasks),
            "task_order": list(self.task_order),
            "state": dict(self.state),
            "walker": dict(self.walker),
            "audit": dict(self.audit),
            "progress": dict(self.progress),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SprintState:
        """Reconstruye desde dict (carga de persistencia)."""
        ssd = {}
        for k, v in data.get("sub_state_data", {}).items():
            if isinstance(v, dict):
                ssd[k] = SubStateData(
                    fields=dict(v.get("fields", {})),
                    completed_at=v.get("completed_at"),
                )
            else:
                ssd[k] = SubStateData()
        # Deserializar node_executions
        ne_list = []
        for ne_dict in data.get("node_executions", []):
            if isinstance(ne_dict, dict):
                ne_list.append(NodeExecution(**{
                    k: v for k, v in ne_dict.items()
                    if k in NodeExecution.__dataclass_fields__
                }))

        return cls(
            version=data.get("version", "5.0"),
            current_node=data.get("current_node", ""),
            current_sub_state=data.get("current_sub_state", ""),
            sub_state_index=data.get("sub_state_index", 0),
            sub_state_data=ssd,
            node_executions=ne_list,
            history=list(data.get("history", [])),
            context=dict(data.get("context", {})),
            updated_at=data.get("updated_at", ""),
            sprint=dict(data.get("sprint", {})),
            tasks=list(data.get("tasks", [])),
            task_order=list(data.get("task_order", [])),
            state=dict(data.get("state", {})),
            walker=dict(data.get("walker", {})),
            audit=dict(data.get("audit", {})),
            progress=dict(data.get("progress", {})),
        )


# ── TaskResult (generico tipado) ───────────────────────────────────────────

T = TypeVar("T")


@dataclass
class TaskResult(Generic[T]):
    """Resultado tipado de una tarea pura.
    
    T define el tipo del data esperado (definido por output_schema).
    SRP: Unica representacion del resultado de una ejecucion.
    """
    ok: bool
    data: T | None = None
    error: "WorkflowError | None" = None
    side_effects: list[dict] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""

    @classmethod
    def success(cls, data: T, **kw: Any) -> "TaskResult[T]":
        return cls(ok=True, data=data, **kw)

    @classmethod
    def failure(cls, error: "WorkflowError", **kw: Any) -> "TaskResult[T]":
        return cls(ok=False, error=error, **kw)


# ── ExecutionContext (trazabilidad) ────────────────────────────────────────

@dataclass
class ExecutionContext:
    """Contexto de ejecucion para toda operacion del engine.
    
    SRP: Transportar metadata de ejecucion sin acoplarse al dominio.
    Toda operacion en application/ recibe este contexto.
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
        import uuid
        return cls(
            session_id=session_id,
            trace_id=kw.pop("trace_id", uuid.uuid4().hex[:12]),
            span_id=kw.pop("span_id", uuid.uuid4().hex[:8]),
            chain_dir=chain_dir,
            **kw,
        )


# ── NodeExecution (ELM/Saga Log) ────────────────────────────────────────────

@dataclass
class NodeExecution:
    """Resultado completo de la ejecucion de un sub-estado.

    Patron Saga Log / ELM: cada entrada representa UNA ejecucion atomica
    de un sub-estado, incluyendo inputs, tarea, side-effects, errores.
    El array completo permite reconstruir el estado reproduciendo eventos.

    Attributes:
        node: Nombre del nodo (ej: "SPRINT.PLANNING")
        sub_state: Nombre del sub-estado (ej: "PLANNING")
        execution_type: auto | llm_task | user_input | manual
        action: TaskRegistry action ejecutada (vacio si no hay)
        submitted_fields: Campos que el agente submitio
        task_ok: True si la tarea se ejecuto OK
        task_result: Resultado de TaskRegistry.execute()
        side_effects: Resultados de SideEffectEngine
        error: Mensaje de error si fallo (None si OK)
        error_type: Tipo de error si fallo (validation, execution, etc.)
        gate: Gate usada para transicionar (solo en transiciones)
        completed_at: Timestamp ISO de finalizacion
        duration_ms: Duracion en milisegundos
    """
    node: str = ""
    sub_state: str = ""
    execution_type: str = ""
    action: str = ""
    submitted_fields: dict[str, Any] = field(default_factory=dict)
    task_ok: bool = True
    task_result: Any = None
    side_effects: list[dict] = field(default_factory=list)
    error: str | None = None
    error_type: str | None = None
    gate: str = ""
    completed_at: str = ""
    duration_ms: int = 0


# ── Domain Event ───────────────────────────────────────────────────────────

@dataclass
class DomainEvent:
    """Evento de dominio en el contexto Workflow Navigation.
    
    SSOT para todos los eventos que puede emitir el engine.
    """
    event_type: str
    timestamp: str = field(default_factory=lambda: _now())
    data: dict[str, Any] = field(default_factory=dict)


# ── Event Types (SSOT) ─────────────────────────────────────────────────────

class DomainEventType:
    """Catalogo de eventos de dominio. SSOT."""
    STATE_TRANSITIONED = "state_transitioned"
    DATA_SUBMITTED = "data_submitted"
    GATE_VALIDATED = "gate_validated"
    AUTO_ADVANCED = "auto_advanced"
    FAILURE_LOOPED = "failure_looped"
    VALIDATION_FAILED = "validation_failed"
    PRECONDITION_FAILED = "precondition_failed"


# ── Helper ─────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
