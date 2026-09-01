"""domain/services/advance.py — Sub-state advancement logic.

SRP: only moves the sub-state index. No scripts, no persistence, no IO.
"""
from __future__ import annotations

from ..entities import WorkflowGraph, WorkflowState


class AdvanceService:
    """Automatic sub-state advancement logic."""

    @staticmethod
    def advance(state: WorkflowState, graph: WorkflowGraph) -> WorkflowState | None:
        """Return new state advanced to next sub-state. None if no more."""
        node = graph.get_node(state.current_node)
        if not node:
            return None
        next_idx = state.sub_state_index + 1
        if next_idx >= len(node.sub_states):
            return None
        next_ss = node.sub_states[next_idx]
        return state.with_node(state.current_node, next_ss.key, next_idx).add_history(
            f"{state.current_node}.{next_ss.key}"
        )

    @staticmethod
    def can_advance_to(state: WorkflowState, graph: WorkflowGraph, target_key: str) -> bool:
        node = graph.get_node(state.current_node)
        if not node:
            return False
        target_idx = node.sub_state_index(target_key)
        return target_idx == state.sub_state_index + 1

    @staticmethod
    def loop_back(state: WorkflowState, graph: WorkflowGraph) -> WorkflowState | None:
        """Return new state stepping back to previous sub-state."""
        node = graph.get_node(state.current_node)
        if not node:
            return None
        prev_idx = state.sub_state_index - 1
        if prev_idx < 0:
            return None
        prev_ss = node.sub_states[prev_idx]
        return state.with_node(state.current_node, prev_ss.key, prev_idx).add_history(
            f"{state.current_node}.{prev_ss.key} (loop)"
        )

    @staticmethod
    def jump_to_sub_state(state: WorkflowState, graph: WorkflowGraph, target_key: str) -> WorkflowState | None:
        """Jump to a specific sub-state WITHIN the current node (on_failure)."""
        node = graph.get_node(state.current_node)
        if not node:
            return None
        target_idx = node.sub_state_index(target_key)
        if target_idx < 0:
            return None
        return state.with_node(state.current_node, target_key, target_idx).add_history(
            f"{state.current_node}.{target_key} (on_failure)"
        )
