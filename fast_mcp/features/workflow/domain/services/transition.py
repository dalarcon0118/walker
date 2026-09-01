"""domain/services/transition.py — State transition application.

Immutable (ELM pattern).
"""
from __future__ import annotations

from ..entities import WorkflowGraph, WorkflowState
from ..errors import NodeNotFoundError


class TransitionService:
    """State transition application. Immutable (ELM pattern)."""

    @staticmethod
    def apply(
        state: WorkflowState,
        graph: WorkflowGraph,
        target_node_id: str,
        target_sub_state: str | None = None,
    ) -> WorkflowState:
        """Return new state after applying the transition."""
        target_node = graph.get_node(target_node_id)
        if not target_node:
            raise NodeNotFoundError(target_node_id)

        if target_sub_state:
            first_key = target_sub_state
        elif target_node.first_sub_state:
            first_key = target_node.first_sub_state.key
        else:
            first_key = "INIT"

        target_idx = target_node.sub_state_index(first_key)
        idx = target_idx if target_idx >= 0 else 0

        new_state = state.with_node(target_node_id, first_key, idx).add_history(
            f"{target_node_id}.{first_key}"
        )

        # Initialize sub-state data if empty
        try:
            existing = new_state.get_sub_state_data(first_key)
            if not existing.fields:
                new_state = new_state.record_submit(first_key, {"status": "pending"})
        except Exception:
            pass

        return new_state
