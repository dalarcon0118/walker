"""domain/entities.py — Entidades del dominio Workflow Navigation.

SSOT de entidades mutables e inmutables que representan el estado
del workflow y sus componentes.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from shared.core.engine.domain.enums import ExecutionType, SubStateMode
from shared.core.engine.domain.values import (
    RetryPolicy,
    SideEffectDef,
    SelectOption,
    SubStateData,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _compensation_type(raw: Any) -> str:
    """Normaliza el campo compensation de un side_effect del grafo.

    El grafo declara compensation como dict {"type": ...} (SPEC-014 Fase 0:
    fuente única — el engine compensa con lo que declara el grafo, nunca un
    mapa hardcodeado interno).
    """
    if isinstance(raw, dict):
        return str(raw.get("type", ""))
    if raw is None:
        return ""
    return str(raw)


# ── Entity: SubState (v3) ─────────────────────────────────────────────────────


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
    on_success: str = ""  # next sub-state key on success
    on_failure: str = ""  # next sub-state key on failure
    retry: RetryPolicy | None = None
    timeout_ms: int = 0
    """Max time to wait before auto-resolving (0 = no timeout)."""

    # ── Wait state / blocking context (P0) ───────────────────────────────
    interrupting: bool = True
    """If True, timeout interrupts the sub-state (boundary event).
    If False, timeout fires as a side-effect without interrupting."""
    on_timeout: str = ""
    """Resolution strategy when timeout expires: skip | force | escalate | compensate."""
    wait_for: list[str] = field(default_factory=list)
    """Events that can resume this wait state (event-based gateway).
    Ej: ["kg_query", "user_input", "external_event"]"""

    # ── Signal events (P1) ────────────────────────────────────────────────
    emits: list[str] = field(default_factory=list)
    """Signals emitted when this sub-state completes successfully.
    Ej: ["kg_updated", "sprint_ready"]"""
    waits_for: list[str] = field(default_factory=list)
    """Signals that can resume this wait state before timeout.
    Ej: ["kg_updated", "approval_received"]"""
    on_signal: str = ""
    """Action when a waited signal arrives: auto_advance | skip | force | escalate."""

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
    prompt_id: str = (
        ""  # Reference to prompts/{prompt_id}.prompt.md (replaces on_entry_message)
    )
    context: dict | None = (
        None  # Context Composer config: {sources: [...], focus: {...}}
    )
    required_tools: list[dict] = field(default_factory=list)
    """Tools que el agente DEBE invocar antes de completar el sub-estado.

    Declaración del gate de auto-consulta (schema workflow):
        [{"id": "ckg", "command_hint": "walker <project>/recall_knowledge '{\"_index\":\"ckg\",\"type\":\"convention\"}'"}]

    Uso (engine):
      - StepOrchestrator renderiza una sección "TOOLS REQUERIDAS" en el
        mensaje al LLM para que las invoque antes de responder.
      - SubmitOrchestrator verifica en el TraceGraph que exista un evento
        kg_query reciente (task/session) para el sub-estado antes de
        aceptar el submit; si no, BLOQUEA (convención CKG doc-28).
    """

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
        self.execution = (
            value if isinstance(value, ExecutionType) else ExecutionType(value)
        )

    def is_auto(self) -> bool:
        return self.execution in (ExecutionType.AUTO, ExecutionType.BOOTSTRAP)

    def is_llm_task(self) -> bool:
        """True si el sub-estado es un `llm_task` (el agente responde con datos)."""
        return self.execution == ExecutionType.LLM_TASK

    def needs_input(self) -> bool:
        return (
            self.execution
            in (ExecutionType.LLM_TASK, ExecutionType.USER_INPUT, ExecutionType.MANUAL)
            and len(self.required_fields) > 0
        )

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> SubState:
        execution = data.get("execution", "manual")
        if isinstance(execution, str):
            execution = ExecutionType(execution)
        side_effects = [
            SideEffectDef(
                type=se.get("type", ""),
                params=dict(se.get("params", {})),
                optional=bool(se.get("optional", False)),
                compensation=_compensation_type(se.get("compensation")),
                idempotency_key=se.get("idempotency_key", ""),
            )
            for se in data.get("side_effects", [])
            if isinstance(se, dict)
        ]
        retry_raw = data.get("retry")
        retry = RetryPolicy(**retry_raw) if isinstance(retry_raw, dict) else None
        options = [
            SelectOption(value=o.get("value", ""), label=o.get("label", ""))
            for o in data.get("options", [])
            if isinstance(o, dict)
        ]
        return cls(
            key=str(data.get("key", "")),
            label=str(data.get("label", "")),
            execution=execution,
            action=str(data.get("action", "")),
            input_schema=dict(data.get("input_schema", {})),
            output_schema=dict(data.get("output_schema", {})),
            side_effects=side_effects,
            on_success=str(data.get("on_success", "")),
            on_failure=str(data.get("on_failure", "")),
            retry=retry,
            timeout_ms=int(data.get("timeout_ms", 0)),
            interrupting=bool(data.get("interrupting", True)),
            on_timeout=str(data.get("on_timeout", "")),
            wait_for=list(data.get("wait_for", [])),
            emits=list(data.get("emits", [])),
            waits_for=list(data.get("waits_for", [])),
            on_signal=str(data.get("on_signal", "")),
            required_fields=list(data.get("required_fields", data.get("required", []))),
            auto_complete_script=str(data.get("auto_complete", "")),
            on_failure_loop=bool(data.get("on_failure_loop", False)),
            order=int(data.get("order", 0)),
            prompt=str(data.get("prompt", "")),
            options=options,
            on_entry_message=str(data.get("on_entry_message", "")),
            prompt_id=str(data.get("prompt_id", "")),
            context=data.get("context"),
            required_tools=list(data.get("required_tools", [])),
            provides=list(data.get("provides", [])),
            needs=list(data.get("needs", [])),
        )


# ── Entity: Gate ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Gate:
    """Puerta de transicion entre nodos o sub-estados.

    Una gate es el unico mecanismo para cambiar de estado en el workflow.

    Attributes:
        contract: Condiciones pre/post que se validan al cruzar la gate.
            pre: expresion que debe cumplirse ANTES de la transicion
                 Ej: "REVIEW.decision == 'approve'"
            post: expresion que debe cumplirse DESPUES de la transicion
                  Ej: "ANALYZE.GATHER.status == 'pending'"
            invariants: condiciones que deben mantenerse durante la transicion
    """

    name: str
    source_node: str
    target_node: str
    target_sub_state: str | None = None
    source_sub_state: str | None = None
    description: str = ""
    gate_validation_script: str | None = None
    gate_valid_exit_codes: list[int] = field(default_factory=lambda: [0])
    gate_error_template: str = "Gate validation failed: $stderr"
    contract: dict | None = None
    """Contracto declarativo: {"pre": "...", "post": "...", "invariants": [...]}.
    Se evalua en TransitionUseCase antes/despues de aplicar la transicion."""
    transform: dict | None = None
    context_updates: list[dict] | None = None


# ── Entity: WorkflowNode ─────────────────────────────────────────────────────


@dataclass
class WorkflowNode:
    """Nodo del pipeline de workflow. Contiene sub-estados secuenciales.

    SRP: Unica responsabilidad = el pipeline de sub-estados y su config.
    NO tiene gates/edges — esas pertenecen al WorkflowGraph.
    """

    node_id: str
    sub_states: list[SubState] = field(default_factory=list)
    label: str = ""
    description: str = ""
    entry_action_script: str = ""
    on_entry_message: str = ""
    provides: list[str] = field(default_factory=list)
    """Contexto que este nodo produce. Se promueve automáticamente al
    global state al transicionar. Todos los sub-estados lo heredan."""

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


# ── Entity: WorkflowGraph ────────────────────────────────────────────────────


@dataclass
class WorkflowGraph:
    """Grafo completo de workflow. SSOT para edges y definicion de nodos.

    SSOT de las edges:
      - edges: lista plana de todas las transiciones entre nodos
      - edges_by_source: índice para búsqueda rápida {source: [Gate]}

    Los nodos NO tienen gates — las edges son propiedad exclusiva del grafo.
    """

    nodes: dict[str, WorkflowNode] = field(default_factory=dict)
    edges: list[Gate] = field(default_factory=list)
    edges_by_source: dict[str, list[Gate]] = field(default_factory=dict)
    initial_state: str = ""
    on_events: dict[str, dict] = field(default_factory=dict)

    def get_node(self, node_id: str) -> WorkflowNode | None:
        return self.nodes.get(node_id)

    def find_gate(self, source: str, gate_name: str) -> Gate | None:
        """Busca una gate (edge) desde un nodo origen por nombre."""
        for g in self.edges_by_source.get(source, []):
            if g.name == gate_name:
                return g
        return None

    def find_event_handler(self, event_name: str) -> dict | None:
        return self.on_events.get(event_name)

    def validate_invariants(self) -> list[str]:
        """Valida invariantes del agregado WorkflowGraph.

        Returns:
            Lista de errores de invariante. Vacía si el grafo es válido.
        """
        errors: list[str] = []

        # Invariante 1: Toda gate debe apuntar a un nodo existente
        for gate in self.edges:
            if gate.target_node and gate.target_node not in self.nodes:
                errors.append(
                    f"Gate '{gate.name}' apunta a nodo inexistente '{gate.target_node}'"
                )

        # Invariante 2: edges_by_source debe ser consistente con edges
        expected_by_source: dict[str, list[Gate]] = {}
        for g in self.edges:
            expected_by_source.setdefault(g.source_node, []).append(g)
        for node_id, expected_gates in expected_by_source.items():
            actual_gates = self.edges_by_source.get(node_id, [])
            if len(actual_gates) != len(expected_gates):
                errors.append(
                    f"edges_by_source inconsistente para nodo '{node_id}': "
                    f"esperado {len(expected_gates)} gates, encontrado {len(actual_gates)}"
                )

        # Invariante 3: initial_state debe existir en nodes (si hay nodos)
        if self.initial_state and self.initial_state not in self.nodes:
            errors.append(
                f"initial_state '{self.initial_state}' no existe en nodes"
            )

        # Invariante 4: No debe haber gates duplicadas (mismo source + name)
        seen_gates: dict[str, str] = {}
        for g in self.edges:
            key = f"{g.source_node}:{g.name}"
            if key in seen_gates:
                errors.append(
                    f"Gate duplicada: '{g.name}' desde nodo '{g.source_node}'"
                )
            seen_gates[key] = g.target_node

        # Invariante 5: Validar flujo de provides/needs (P2 Process Data Store)
        try:
            from shared.core.engine.application.context_resolver import ContextResolver
            ctx_errors = ContextResolver().validate_graph(self)
            errors.extend(ctx_errors)
        except Exception:
            pass

        return errors

    def is_consistent(self) -> bool:
        """Retorna True si el grafo pasa todas las validaciones de invariantes."""
        return len(self.validate_invariants()) == 0

    @staticmethod
    def _sub_state_steps(node: Any, node_id: str) -> list[dict]:
        """Steps 'process' para los sub-estados de un nodo."""
        from shared.core.engine.domain.services import RequiredFieldResolver

        steps: list[dict] = []
        for ss in node.sub_states:
            exec_type = ss.execution.value if ss.execution else "manual"
            steps.append(
                {
                    "action": "process",
                    "node": node_id,
                    "sub_state": ss.key,
                    "execution": exec_type,
                    "label": ss.label or ss.key,
                    "required_fields": RequiredFieldResolver.derive(ss),
                }
            )
        return steps

    @staticmethod
    def _build_flow(flow_steps: list[dict]) -> dict:
        """Construye el dict de un flujo con nombre compacto."""
        labels = []
        for s in flow_steps:
            if s["action"] == "process":
                labels.append(f"{s['node']}.{s['sub_state']}")
            elif s["action"] == "transition":
                labels.append(f"[{s['gate']}]")
        return {
            "name": " → ".join(labels),
            "steps": flow_steps,
            "node_count": len(
                set(s.get("node", "") for s in flow_steps if s.get("node"))
            ),
        }

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
            steps.append(
                {
                    "action": "transition",
                    "gate": gate.name,
                    "from": f"{gate.source_node}",
                    "to": f"{target}.{target_sub}",
                    "description": gate.description or f"Transition via {gate.name}",
                }
            )

            # Walk sub-states of target node
            for ss in target_node.sub_states:
                exec_type = ss.execution.value if ss.execution else "manual"
                from shared.core.engine.domain.services import RequiredFieldResolver

                required = RequiredFieldResolver.derive(ss)
                steps.append(
                    {
                        "action": "process",
                        "node": target,
                        "sub_state": ss.key,
                        "execution": exec_type,
                        "label": ss.label or ss.key,
                        "required_fields": required,
                    }
                )

            # Follow outgoing gates from target node
            for next_gate in graph.edges_by_source.get(target, []):
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
        init_steps = self._sub_state_steps(start_node, self.initial_state)

        # Follow each outgoing gate from initial node as a separate flow
        for gate in self.edges_by_source.get(self.initial_state, []):
            visited.clear()
            visited.add(self.initial_state)

            flow_steps = list(init_steps)
            flow_steps.extend(_follow(gate))

            # Skip flows that couldn't follow the gate (invalid target, cycle, etc.)
            if len(flow_steps) <= len(init_steps):
                continue

            flows.append(self._build_flow(flow_steps))

        # Handle case where initial node has no gates (isolated node)
        if not self.edges_by_source.get(self.initial_state) and init_steps:
            flows.append(
                {
                    "name": " → ".join(
                        f"{self.initial_state}.{s['sub_state']}" for s in init_steps
                    ),
                    "steps": init_steps,
                    "node_count": 1,
                }
            )

        return flows


# ── Entity: WorkflowState (inmutable, ELM pattern) ───────────────────────────


@dataclass(frozen=True)
class WorkflowState:
    """Estado actual del workflow. INMUTABLE (ELM pattern).

    Cada operación retorna una NUEVA instancia. El estado original
    nunca se modifica. Esto permite time-travel, testing determinístico,
    y saga/compensación natural.

    PURA: No contiene datos de negocio (sprint, tasks, project, etc.).
    El estado de negocio del spring vive en el DeltaGraph (SpringService),
    no en este modelo. sprint-state.json/SprintState fueron eliminados.
    """

    current_node: str = ""
    current_sub_state: str = ""
    sub_state_index: int = 0
    sub_state_data: dict[str, SubStateData] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    version: str = "5.0"
    updated_at: str = ""
    node_executions: list[NodeExecution] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    """Conflictos detectados y resueltos en este estado."""

    # ── Wait state / blocking context (P0) ───────────────────────────────
    blocked_at: str = ""
    """Current blocking location: 'SPRINT.USER_GATE' or '' if not blocked."""
    block_reason: str = ""
    """Human-readable reason for the current block."""
    block_since: str = ""
    """ISO timestamp when the block started."""
    pending_events: list[str] = field(default_factory=list)
    """Events that can unblock the current wait state."""
    block_timeout_ms: int = 0
    """Timeout for the current block (0 = no timeout)."""

    # ── Signal events (P1) ────────────────────────────────────────────────
    pending_signals: list[str] = field(default_factory=list)
    """Signals waiting to be processed for the current sub-state."""
    signal_history: list[dict] = field(default_factory=list)
    """History of emitted/received signals for observability."""

    # ── Process Data Store (P2) ──────────────────────────────────────────
    process_data: dict[str, Any] = field(default_factory=dict)
    """Typed data produced/consumed by the workflow.
    
    Examples:
      process_data = {
        "project_id": "walker",
        "specs": [...],
        "current_spec_index": 0,
        "kg_consulted_layers": ["cpg", "ckg"]
      }
    """

    def reconcile_index(self, graph: WorkflowGraph) -> tuple["WorkflowState", list[str]]:
        """Reconcilia el invariante sub_state_index ↔ current_sub_state (GAP-1).

        Analogía git: al cargar el "repo" del workflow, validamos que la
        rama apunte a un commit coherente. Si `current_sub_state` no se
        corresponde con `sub_state_index` en el nodo actual, corregimos el
        índice a partir del nombre (autoridad = current_sub_state, que es
        el sub-estado real donde dejó el agente).

        Inmutable (ELM): retorna (nuevo estado, warnings). No muta self.

        Returns:
            (WorkflowState reconciliado, lista de warnings de reconciliación)
        """
        if not graph or not self.current_node or not self.current_sub_state:
            return self, []
        node = graph.get_node(self.current_node)
        if not node:
            return self, []
        expected = node.sub_state_index(self.current_sub_state)
        if expected < 0:
            # El nombre no está en el nodo → no podemos reconciliar de forma segura.
            return self, [
                f"[reconcile] sub-estado '{self.current_sub_state}' no existe "
                f"en nodo '{self.current_node}'"
            ]
        if self.sub_state_index == expected:
            return self, []
        return (
            replace(self, sub_state_index=expected, updated_at=_now()),
            [
                f"[reconcileIndex] corrió ({self.current_node}: "
                f"'{self.current_sub_state}' index {self.sub_state_index} → {expected})"
            ],
        )

    def get_sub_state_data(self, key: str | None = None) -> SubStateData:
        k = key or self.current_sub_state
        if k in self.sub_state_data:
            return self.sub_state_data[k]
        if self.current_node and "." not in k:
            prefixed = f"{self.current_node}.{k}"
            if prefixed in self.sub_state_data:
                return self.sub_state_data[prefixed]
        return SubStateData()

    def record_submit(self, sub_key: str, fields: dict[str, Any]) -> WorkflowState:
        """Retorna nuevo WorkflowState con los datos persistidos.

        ELM pattern: no muta self, retorna nueva instancia.
        """
        existing = self.get_sub_state_data(sub_key)
        new_fields = dict(existing.fields)
        new_fields.update(fields)
        new_ssd = dict(self.sub_state_data)
        new_ssd[sub_key] = SubStateData(fields=new_fields, completed_at=_now())
        return replace(self, sub_state_data=new_ssd, updated_at=_now())

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
    ) -> WorkflowState:
        """Retorna nuevo WorkflowState con la ejecución registrada.

        ELM/Saga Log: agrega una entrada al final de node_executions.
        """
        now = _now()
        new_execs = list(self.node_executions)
        new_execs.append(
            NodeExecution(
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
                completed_at=now,
                duration_ms=duration_ms,
            )
        )
        return replace(self, node_executions=new_execs, updated_at=now)

    def add_history(self, entry: str) -> WorkflowState:
        """Retorna nuevo WorkflowState con la entrada de historial agregada."""
        new_history = list(self.history)
        new_history.append(entry)
        return replace(self, history=new_history, updated_at=_now())

    def with_context(self, key: str, value: Any) -> WorkflowState:
        """Retorna nuevo WorkflowState con un valor en context."""
        new_ctx = dict(self.context)
        new_ctx[key] = value
        return replace(self, context=new_ctx)

    def with_node(self, node: str, sub_state: str, index: int = 0) -> WorkflowState:
        """Retorna nuevo WorkflowState con posición cambiada."""
        return replace(
            self,
            current_node=node,
            current_sub_state=sub_state,
            sub_state_index=index,
        )

    def to_dict(self) -> dict[str, Any]:
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
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowState:
        ssd = {}
        for k, v in data.get("sub_state_data", {}).items():
            if isinstance(v, dict):
                ssd[k] = SubStateData(
                    fields=dict(v.get("fields", {})),
                    completed_at=v.get("completed_at"),
                )
            else:
                ssd[k] = SubStateData()
        ne_list = []
        for ne_dict in data.get("node_executions", []):
            if isinstance(ne_dict, dict):
                ne_list.append(
                    NodeExecution(
                        **{
                            k: v
                            for k, v in ne_dict.items()
                            if k in NodeExecution.__dataclass_fields__
                        }
                    )
                )
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
        )


# ── NodeExecution (ELM/Saga Log) ─────────────────────────────────────────────


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


# ── Domain Event ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DomainEvent:
    """Evento de dominio en el contexto Workflow Navigation.

    SSOT para todos los eventos que puede emitir el engine.
    """

    event_type: str
    timestamp: str = field(default_factory=lambda: _now())
    data: dict[str, Any] = field(default_factory=dict)


# ── Helper ───────────────────────────────────────────────────────────────────


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


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
    "DomainEventType",
]
