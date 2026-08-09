"""domain/services/conflict_resolver.py — Resolución de conflictos del workflow.

P1: Complementa BlockResolver con estrategias específicas de dominio:
- skip/force: delegan en BlockResolver (engine)
- edit/merge: delegan en ConflictResolver de delta_tracking (specs)

SRP: esta clase opera sobre WorkflowState y WorkflowGraph (engine).
Las operaciones sobre specs (edit/merge) se delegan a delta_tracking.
"""
from __future__ import annotations

from typing import Any

from returns.pipeline import is_successful
from returns.result import Result

from features.engine.domain.entities import WorkflowState, WorkflowGraph
from features.engine.domain.services.block_resolver import BlockResolver
from features.engine.domain.errors import ErrorType


class WorkflowConflictResolver:
    """Resuelve conflictos del workflow aplicando estrategias.

    Estrategias:
    - skip: marca sub-estado como completado sin datos (BlockResolver)
    - force: permite submit con advertencia (BlockResolver)
    - escalate: transiciona a nodo de escalación (BlockResolver)
    - compensate: ejecuta compensación (BlockResolver)
    - edit: actualiza spec en delta_tracking (delegado)
    - merge: integra specs sin perder información (delegado)
    """

    @staticmethod
    def resolve(
        state: WorkflowState,
        strategy: str,
        payload: dict[str, Any],
        graph: WorkflowGraph | None = None,
    ) -> Result[WorkflowState, str]:
        """Resuelve un conflicto aplicando la estrategia indicada.

        Args:
            state: Estado actual del workflow
            strategy: Estrategia (skip | force | escalate | compensate)
            payload: Datos adicionales para la resolución
            graph: Grafo de workflow (necesario para escalate)

        Returns:
            Success(new_state) si la resolución fue exitosa
            Failure(reason) si no se pudo resolver
        """
        if strategy in ("edit", "merge"):
            return WorkflowConflictResolver._resolve_spec_strategy(
                state, strategy, payload
            )
        return BlockResolver.resolve(state, strategy, payload, graph=graph)

    @staticmethod
    def _resolve_spec_strategy(
        state: WorkflowState,
        strategy: str,
        payload: dict[str, Any],
    ) -> Result[WorkflowState, str]:
        """Delega en delta_tracking para estrategias de specs."""
        try:
            from features.delta_tracking.domain.services.conflict_resolver import (
                ConflictResolver as DeltaConflictResolver,
            )
            # DeltaConflictResolver opera sobre dicts, no entidades
            state_dict = state.to_dict() if hasattr(state, "to_dict") else state
            new_state_dict, _ = DeltaConflictResolver.resolve(
                conflict=None,
                strategy=strategy,
                payload=payload,
                workflow_state=state_dict,
                specs=[],
            )
            if isinstance(new_state_dict, dict):
                return WorkflowConflictResolver._dict_to_state_result(new_state_dict, state)
            return WorkflowConflictResolver._dict_to_state_result(new_state_dict, state)
        except Exception as e:
            return Failure(f"spec_strategy_failed: {type(e).__name__}: {e}")

    @staticmethod
    def _dict_to_state_result(data: dict, fallback: WorkflowState) -> Result[WorkflowState, str]:
        """Convierte dict a WorkflowState, preservando campos nuevos."""
        try:
            from features.engine.domain.entities import WorkflowState as WS
            return Success(WS(
                current_node=data.get("current_node", fallback.current_node),
                current_sub_state=data.get("current_sub_state", fallback.current_sub_state),
                sub_state_index=data.get("sub_state_index", fallback.sub_state_index),
                sub_state_data={k: v for k, v in data.get("sub_state_data", {}).items()},
                history=data.get("history", fallback.history),
                context=data.get("context", fallback.context),
                version=data.get("version", fallback.version),
                updated_at=data.get("updated_at", fallback.updated_at),
                node_executions=fallback.node_executions,
                conflicts=data.get("conflicts", fallback.conflicts),
                blocked_at=data.get("blocked_at", fallback.blocked_at),
                block_reason=data.get("blocked_at", fallback.block_reason),
                block_since=data.get("block_since", fallback.block_since),
                pending_events=data.get("pending_events", fallback.pending_events),
                block_timeout_ms=data.get("block_timeout_ms", fallback.block_timeout_ms),
                pending_signals=data.get("pending_signals", fallback.pending_signals),
                signal_history=data.get("signal_history", fallback.signal_history),
                process_data=data.get("process_data", fallback.process_data),
            ))
        except Exception as e:
            return Failure(f"state_conversion_failed: {type(e).__name__}: {e}")


from returns.result import Success, Failure
