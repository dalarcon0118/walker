"""domain/services/validation.py — Domain validation rules.

SRP: only validates workflow business rules. SSOT for all validation logic.
"""
from __future__ import annotations

from ..entities import WorkflowGraph, WorkflowState, Gate
from ..enums import ExecutionType


class ValidationService:
    """Domain validation rules."""

    @staticmethod
    def check_transition_preconditions(
        state: WorkflowState,
        graph: WorkflowGraph,
        gate_name: str,
    ) -> list[str]:
        """Check preconditions for a transition. Returns list of errors."""
        errors: list[str] = []
        node = graph.get_node(state.current_node)
        if not node:
            return [f"Current node '{state.current_node}' not in graph"]

        for i, ss in enumerate(node.sub_states):
            if i > state.sub_state_index:
                break
            sub_data = state.get_sub_state_data(ss.key)
            # `required_fields` explícito o, si no está, el `required` del
            # input_schema. Antes solo miraba el primero: un `llm_task` que
            # declaraba el informe en `input_schema.required` se podía saltear
            # con `advance` (medido 2026-11-26: SPRINT.REPORT pasó a RETRO sin
            # summary/metrics/risks/next). `required` significa requerido.
            required = list(ss.required_fields or [])
            execution = getattr(ss, "execution", "")
            execution = getattr(execution, "value", execution) or ""
            if (not required and i == state.sub_state_index
                    and execution != ExecutionType.USER_INPUT.value):
                # Solo el sub-estado ACTUAL y solo si no es una decisión: el
                # usuario tiene su propio gate (user_input_required, más claro
                # que un validation_failed). Revalidar los anteriores exigiría
                # campos que el flujo ya consumió y que algunos nodos no
                # guardan en sub_state_data.
                required = list((getattr(ss, "input_schema", {}) or {}).get("required") or [])
            if required:
                missing = [f for f in required if not sub_data.is_field_complete(f)]
                if missing:
                    errors.append(f"[{state.current_node}.{ss.key}] missing: {', '.join(missing)}")
        return errors

    @staticmethod
    def is_node_complete(state: WorkflowState, graph: WorkflowGraph) -> bool:
        node = graph.get_node(state.current_node)
        if not node:
            return True
        return node.is_complete(state.sub_state_index)

    @staticmethod
    def can_advance(state: WorkflowState, graph: WorkflowGraph) -> bool:
        node = graph.get_node(state.current_node)
        if not node:
            return False
        return state.sub_state_index < len(node.sub_states) - 1

    @staticmethod
    def get_available_gates(state: WorkflowState, graph: WorkflowGraph) -> list[Gate]:
        """Gates válidos desde el sub-estado actual.

        `available_from` es la fuente explícita (lista de sub-estados); si no
        está, cae a `source_sub_state` (legacy) y, si tampoco, el gate queda
        global. Los gates globales permitían saltarse el trabajo: `task_complete`
        desde `QA_RUN`, `implementation_done` desde `OBSERVE`. El grafo canónico
        ya declara `available_from` en todos; el invariante 7 lo vigila.
        """
        edges = graph.edges_by_source.get(state.current_node, [])
        out: list[Gate] = []
        for gate in edges:
            allowed = list(getattr(gate, "available_from", None) or [])
            if allowed:
                if state.current_sub_state in allowed:
                    out.append(gate)
                continue
            if getattr(gate, "source_sub_state", ""):
                if gate.source_sub_state == state.current_sub_state:
                    out.append(gate)
                continue
            out.append(gate)
        return out

    @staticmethod
    def resolve_xor_by_intent(state: WorkflowState, graph: WorkflowGraph, intent: str) -> Gate | None:
        for g in ValidationService.get_available_gates(state, graph):
            if g.name == intent:
                return g
        return None
