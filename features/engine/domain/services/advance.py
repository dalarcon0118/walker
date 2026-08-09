"""domain/services/advance.py — AdvanceService.

Logica de avance automatico entre sub-estados.
SRP: Unica responsabilidad = mover el indice de sub-estado.
No ejecuta scripts, no persiste, no imprime.
"""
from __future__ import annotations

from features.engine.domain.entities import WorkflowGraph, WorkflowState


class AdvanceService:
    """Logica de avance automatico entre sub-estados."""

    @staticmethod
    def advance(state: WorkflowState, graph: WorkflowGraph) -> WorkflowState | None:
        """Retorna nuevo estado avanzado al siguiente sub-estado.

        ELM pattern: no muta state, retorna nueva instancia.
        Retorna None si ya no hay mas sub-estados.
        """
        node = graph.get_node(state.current_node)
        if not node:
            return None

        next_idx = state.sub_state_index + 1
        if next_idx >= len(node.sub_states):
            return None

        next_ss = node.sub_states[next_idx]
        next_key = f"{state.current_node}.{next_ss.key}"
        return state.with_node(state.current_node, next_ss.key, next_idx).add_history(
            next_key
        )

    @staticmethod
    def can_advance_to(
        state: WorkflowState, graph: WorkflowGraph, target_key: str
    ) -> bool:
        node = graph.get_node(state.current_node)
        if not node:
            return False
        target_idx = node.sub_state_index(target_key)
        if target_idx < 0:
            return False
        return target_idx == state.sub_state_index + 1

    @staticmethod
    def loop_back(
        state: WorkflowState,
        graph: WorkflowGraph,
        current_sub_key: str,
    ) -> WorkflowState | None:
        """Retorna nuevo estado retrocediendo al sub-estado anterior."""
        node = graph.get_node(state.current_node)
        if not node:
            return None

        prev_idx = state.sub_state_index - 1
        if prev_idx < 0:
            return None

        prev_ss = node.sub_states[prev_idx]
        prev_key = f"{state.current_node}.{prev_ss.key} (loop)"
        return state.with_node(state.current_node, prev_ss.key, prev_idx).add_history(
            prev_key
        )

    @staticmethod
    def jump_to_sub_state(
        state: WorkflowState,
        graph: WorkflowGraph,
        target_key: str,
    ) -> WorkflowState | None:
        """Salta a un sub-estado especifico DENTRO del nodo actual.

        Usado por on_failure: cuando un sub-estado falla, el workflow
        retrocede/avanza al sub-estado indicado (si existe en el nodo).
        Ej: PLAN_APPROVAL.on_failure=PLANNING → salta a PLANNING.

        Retorna None si el target no existe en el nodo actual.
        """
        node = graph.get_node(state.current_node)
        if not node:
            return None
        target_idx = node.sub_state_index(target_key)
        if target_idx < 0:
            return None
        history_key = f"{state.current_node}.{target_key} (on_failure)"
        return state.with_node(state.current_node, target_key, target_idx).add_history(
            history_key
        )


__all__ = ["AdvanceService"]
