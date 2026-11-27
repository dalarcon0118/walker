"""domain/entities.py — Workflow domain entities (simplified for fast_mcp/).

SSOT for mutable and immutable entities representing workflow state.
Simplifications vs mcp/:
- Removed generate_flows() (CLI-only)
- Simplified SubState.from_dict()
- Removed graph validation that depends on ports
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .enums import ExecutionType
from .values import RetryPolicy, SideEffectDef, SelectOption, SubStateData


def _compensation_type(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(raw.get("type", ""))
    if raw is None:
        return ""
    return str(raw)


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── SubState ──────────────────────────────────────────────────────────────

@dataclass
class SubState:
    """Step within a workflow node."""
    key: str
    label: str = ""
    execution: ExecutionType = ExecutionType.MANUAL
    action: str = ""  # TaskRegistry action name
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    side_effects: list[SideEffectDef] = field(default_factory=list)
    on_success: str = ""
    on_failure: str = ""
    retry: RetryPolicy | None = None
    timeout_ms: int = 0
    interrupting: bool = True
    on_timeout: str = ""
    required_fields: list[str] = field(default_factory=list)
    auto_complete_script: str = ""
    provides: list[str] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)
    prompt: str = ""
    options: list[SelectOption] = field(default_factory=list)
    prompt_id: str = ""
    # Decisión del usuario: qué campo la lleva y a qué gate mapea cada valor.
    # Sin esto el router deducía el gate del NOMBRE (`needs_refine` → refine) y
    # las decisiones cuyo gate no contiene el token quedaban inertes.
    decision_field: str = "decision"
    decision_map: dict[str, str] = field(default_factory=dict)
    on_entry_message: str = ""
    context: dict | None = None
    """Context Composer config: {sources: [...], focus: {...}}"""
    required_tools: list[dict] = field(default_factory=list)
    # Intención de skills para esta fase (needs/tags/policy). El workflow NO
    # declara ids: los skills dependen de lo que tenga el agente.
    skill_intent: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.execution, str):
            self.execution = ExecutionType(self.execution)

    @property
    def mode(self) -> ExecutionType:
        return self.execution

    def is_auto(self) -> bool:
        return self.execution in (ExecutionType.AUTO, ExecutionType.BOOTSTRAP)

    def is_llm_task(self) -> bool:
        return self.execution == ExecutionType.LLM_TASK

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubState:
        execution = data.get("execution", ExecutionType.MANUAL.value)
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
            required_fields=list(data.get("required_fields", data.get("required", []))),
            auto_complete_script=str(data.get("auto_complete", "")),
            prompt=str(data.get("prompt", "")),
            options=options,
            prompt_id=str(data.get("prompt_id", "")),
            decision_field=str(data.get("decision_field", "decision")),
            decision_map=dict(data.get("decision_map") or {}),
            on_entry_message=str(data.get("on_entry_message", "")),
            context=data.get("context"),
            required_tools=list(data.get("required_tools", [])),
            provides=list(data.get("provides", [])),
            needs=list(data.get("needs", [])),
        )


# ── Gate ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Gate:
    """Transition gate BETWEEN nodes (inter-node).

    A gate is the ONLY mechanism to change NODE in the workflow.
    """
    name: str
    source_node: str
    target_node: str
    target_sub_state: str | None = None
    source_sub_state: str | None = None
    # Sub-estados desde los que el gate es válido. Vacío = legacy (cualquiera).
    available_from: list[str] = field(default_factory=list)
    description: str = ""
    #: `advance` = mueve el flujo principal (no puede salir con una acción del
    #: paso actual sin ejecutar); `abandon` = salida legítima que deja trabajo
    #: pendiente (refine, cancel, back_to_*). Declarado en el manifiesto.
    intent: str = "advance"
    gate_validation_script: str | None = None
    gate_valid_exit_codes: list[int] = field(default_factory=lambda: [0])
    contract: dict | None = None
    transform: dict | None = None
    context_updates: list[dict] | None = None


# ── WorkflowNode ──────────────────────────────────────────────────────────

@dataclass
class WorkflowNode:
    """Workflow pipeline node. Contains sequential sub-states."""
    node_id: str
    sub_states: list[SubState] = field(default_factory=list)
    label: str = ""
    description: str = ""
    provides: list[str] = field(default_factory=list)

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


# ── WorkflowGraph ─────────────────────────────────────────────────────────

@dataclass
class WorkflowGraph:
    """Complete workflow graph. SSOT for edges and node definitions."""
    nodes: dict[str, WorkflowNode] = field(default_factory=dict)
    edges: list[Gate] = field(default_factory=list)
    edges_by_source: dict[str, list[Gate]] = field(default_factory=dict)
    initial_state: str = ""
    on_events: dict[str, dict] = field(default_factory=dict)

    def get_node(self, node_id: str) -> WorkflowNode | None:
        return self.nodes.get(node_id)

    def find_gate(self, source: str, gate_name: str) -> Gate | None:
        for g in self.edges_by_source.get(source, []):
            if g.name == gate_name:
                return g
        return None

    def validate_invariants(self) -> list[str]:
        """Validate graph invariants. Returns list of errors (empty = valid)."""
        errors: list[str] = []

        # Invariant 1: every gate points to an existing node
        for gate in self.edges:
            if gate.target_node and gate.target_node not in self.nodes:
                errors.append(f"Gate '{gate.name}' points to non-existent node '{gate.target_node}'")

        # Invariant 2: edges_by_source consistent with edges
        expected: dict[str, list[Gate]] = {}
        for g in self.edges:
            expected.setdefault(g.source_node, []).append(g)
        for node_id, expected_gates in expected.items():
            actual = self.edges_by_source.get(node_id, [])
            if len(actual) != len(expected_gates):
                errors.append(f"edges_by_source inconsistent for '{node_id}'")

        # Invariant 3: initial_state exists
        if self.initial_state and self.initial_state not in self.nodes:
            errors.append(f"initial_state '{self.initial_state}' not in nodes")

        # Invariant 4: no duplicate gates
        seen: dict[str, str] = {}
        for g in self.edges:
            key = f"{g.source_node}:{g.name}"
            if key in seen:
                errors.append(f"Duplicate gate: '{g.name}' from '{g.source_node}'")
            seen[key] = g.target_node

        # Invariant 5: intra-node routing (on_success/on_failure targets exist)
        for node_id, node in self.nodes.items():
            sub_keys = {ss.key for ss in node.sub_states}
            gates = {g.name for g in self.edges_by_source.get(node_id, [])}
            for ss in node.sub_states:
                for field_name, target in (("on_success", ss.on_success), ("on_failure", ss.on_failure)):
                    if target and target not in sub_keys:
                        errors.append(
                            f"Node '{node_id}' sub '{ss.key}': {field_name} "
                            f"points to non-existent '{target}'"
                        )
                # Invariant 6: decision_map apunta a gates del mismo nodo
                for value, gate_name in (ss.decision_map or {}).items():
                    if gate_name not in gates:
                        errors.append(
                            f"Node '{node_id}' sub '{ss.key}': decision_map['{value}'] "
                            f"points to non-existent gate '{gate_name}'"
                        )

        # Invariant 7: `available_from` referencia sub-estados del nodo origen
        for gate in self.edges:
            source = self.nodes.get(gate.source_node)
            if source is None or not gate.available_from:
                continue
            sub_keys = {ss.key for ss in source.sub_states}
            for key in gate.available_from:
                if key not in sub_keys:
                    errors.append(
                        f"Gate '{gate.name}' from '{gate.source_node}': "
                        f"available_from '{key}' is not a sub-state"
                    )

        # Invariant 8: workflow.on no se ejecuta. Antes se cargaba en
        # `on_events` y nadie lo leía: 8 handlers declarados que nunca corrían.
        if self.on_events:
            errors.append(
                "workflow.on no está implementado: usá on_success/on_failure y "
                "gates. Declararlo acá simula un ruteo que el motor no ejecuta."
            )

        return errors


# ── WorkflowState (immutable, ELM pattern) ────────────────────────────────

@dataclass(frozen=True)
class WorkflowState:
    """Current workflow state. IMMUTABLE (ELM pattern).

    Every operation returns a NEW instance. The original is never modified.
    """
    current_node: str = ""
    current_sub_state: str = ""
    sub_state_index: int = 0
    sub_state_data: dict[str, SubStateData] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    version: str = "5.0"
    revision: int = 0  # optimistic concurrency token
    updated_at: str = ""
    node_executions: list[NodeExecution] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    blocked_at: str = ""
    block_reason: str = ""
    block_since: str = ""
    pending_events: list[str] = field(default_factory=list)
    block_timeout_ms: int = 0
    process_data: dict[str, Any] = field(default_factory=dict)

    def reconcile_index(self, graph: WorkflowGraph) -> tuple["WorkflowState", list[str]]:
        """Reconcile sub_state_index ↔ current_sub_state (GAP-1)."""
        if not graph or not self.current_node or not self.current_sub_state:
            return self, []
        node = graph.get_node(self.current_node)
        if not node:
            return self, []
        expected = node.sub_state_index(self.current_sub_state)
        if expected < 0:
            return self, [f"[reconcile] sub '{self.current_sub_state}' not in node '{self.current_node}'"]
        if self.sub_state_index == expected:
            return self, []
        return (
            replace(self, sub_state_index=expected, updated_at=_now()),
            [f"[reconcileIndex] '{self.current_node}': index {self.sub_state_index} → {expected}"],
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
        return replace(self, history=list(self.history) + [entry], updated_at=_now())

    def with_context(self, key: str, value: Any) -> WorkflowState:
        new_ctx = dict(self.context)
        new_ctx[key] = value
        return replace(self, context=new_ctx, updated_at=_now())

    def with_node(self, node: str, sub_state: str, index: int = 0) -> WorkflowState:
        return replace(self, current_node=node, current_sub_state=sub_state, sub_state_index=index)

    def to_dict(self) -> dict[str, Any]:
        ssd = {}
        for k, v in self.sub_state_data.items():
            ssd[k] = {"fields": dict(v.fields), "completed_at": v.completed_at}
        return {
            "version": self.version,
            "revision": self.revision,
            "current_node": self.current_node,
            "current_sub_state": self.current_sub_state,
            "sub_state_index": self.sub_state_index,
            "sub_state_data": ssd,
            "node_executions": [ne.__dict__ for ne in self.node_executions],
            "history": list(self.history),
            "context": dict(self.context),
            "updated_at": self.updated_at or _now(),
            "blocked_at": self.blocked_at,
            "block_reason": self.block_reason,
            "block_since": self.block_since,
            "pending_events": list(self.pending_events),
            "block_timeout_ms": self.block_timeout_ms,
            "process_data": dict(self.process_data),
            "conflicts": [dict(c) for c in self.conflicts],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowState:
        ssd = {}
        for k, v in data.get("sub_state_data", {}).items():
            if isinstance(v, dict):
                ssd[k] = SubStateData(fields=dict(v.get("fields", {})), completed_at=v.get("completed_at"))
            else:
                ssd[k] = SubStateData()
        ne_list = []
        for ne_dict in data.get("node_executions", []):
            if isinstance(ne_dict, dict):
                ne_list.append(NodeExecution(**{k: v for k, v in ne_dict.items() if k in NodeExecution.__dataclass_fields__}))
        return cls(
            version=data.get("version", "5.0"),
            revision=int(data.get("revision", 0) or 0),
            current_node=data.get("current_node", ""),
            current_sub_state=data.get("current_sub_state", ""),
            sub_state_index=data.get("sub_state_index", 0),
            sub_state_data=ssd,
            node_executions=ne_list,
            history=list(data.get("history", [])),
            context=dict(data.get("context", {})),
            updated_at=data.get("updated_at", ""),
            blocked_at=data.get("blocked_at", ""),
            block_reason=data.get("block_reason", ""),
            block_since=data.get("block_since", ""),
            pending_events=list(data.get("pending_events", [])),
            block_timeout_ms=int(data.get("block_timeout_ms", 0) or 0),
            # `pending_signals`/`signal_history` se ignoran: el motor nunca los
            # procesó. Se aceptan estados viejos sin romper la carga.
            process_data=dict(data.get("process_data", {})),
            conflicts=[dict(c) for c in data.get("conflicts", [])],
        )


# ── NodeExecution ─────────────────────────────────────────────────────────

@dataclass
class NodeExecution:
    """Result of a sub-state execution (Saga Log / ELM)."""
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
