"""domain/services/block_resolver.py — Resolución de bloqueos del workflow.

P0: Wait states, timeouts, and resolution strategies.
Inspirado en BPMN: Timer Boundary Events, Event-Based Gateways, Escalation, Compensation.

El engine es responsable de CUÁNDO y CÓMO resolver bloqueos.
delta_tracking es responsable de QUÉ se requiere (contratos).

Usa Result monad (returns) para evitar excepciones como control de flujo.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from returns.result import Failure, Result, Success

from core.engine.domain.entities import WorkflowState, WorkflowGraph
from core.engine.domain.errors import (
    NodeNotFoundError,
    SubStateNotFoundError,
    TransitionBlockedError,
)


class BlockResolver:
    """Resuelve bloqueos del workflow sin desincronizar el estado.

    Estrategias:
    - skip: marca sub-estado como completado sin datos
    - force: permite el submit con advertencia
    - wait: mantiene el bloqueo (default)
    - escalate: transiciona a nodo de escalación
    - compensate: ejecuta compensación y retrocede

    Todas las estrategias retornan Result[WorkflowState, str]:
    - Success(state): resolución aplicada
    - Failure(reason): no se pudo resolver (sin excepción)
    """

    @staticmethod
    def is_blocked(state: WorkflowState) -> bool:
        """Verifica si el workflow está bloqueado."""
        return bool(state.blocked_at)

    @staticmethod
    def can_unblock(state: WorkflowState, event: str) -> bool:
        """Verifica si un evento puede desbloquear el wait state actual."""
        if not state.blocked_at:
            return False
        return event in state.pending_events

    @staticmethod
    def resolve(
        state: WorkflowState,
        strategy: str,
        payload: dict[str, Any],
        graph: WorkflowGraph | None = None,
    ) -> Result[WorkflowState, str]:
        """Aplica estrategia de resolución y retorna Result.

        Args:
            state: Estado actual del workflow
            strategy: Estrategia a aplicar (skip | force | wait | escalate | compensate)
            payload: Datos adicionales para la resolución
            graph: Grafo de workflow (necesario para escalate)

        Returns:
            Success(new_state) si la resolución fue exitosa
            Failure(reason) si no se pudo resolver
        """
        try:
            now = datetime.now(timezone.utc).isoformat()

            if strategy == "skip":
                return Success(BlockResolver._resolve_skip(state, payload, now))
            elif strategy == "force":
                return Success(BlockResolver._resolve_force(state, payload, now))
            elif strategy == "escalate":
                result = BlockResolver._resolve_escalate(state, payload, now, graph)
                return Success(result)
            elif strategy == "compensate":
                return Success(BlockResolver._resolve_compensate(state, payload, now))
            else:
                # wait: mantiene el bloqueo, registra intento
                return Success(BlockResolver._resolve_wait(state, payload, now))
        except TransitionBlockedError as e:
            return Failure(f"transition_blocked: {e}")
        except NodeNotFoundError as e:
            return Failure(f"node_not_found: {e}")
        except SubStateNotFoundError as e:
            return Failure(f"sub_state_not_found: {e}")
        except Exception as e:
            return Failure(f"unexpected: {type(e).__name__}: {e}")

    @staticmethod
    def _resolve_skip(
        state: WorkflowState, payload: dict[str, Any], now: str
    ) -> WorkflowState:
        """Marca sub-estado como completado sin datos."""
        sub_state_key = state.current_sub_state or payload.get("sub_state", "")
        new_data = dict(state.sub_state_data)
        sub_data = dict(
            new_data.get(sub_state_key, {}).fields if sub_state_key in new_data else {}
        )
        sub_data["_skipped"] = True
        sub_data["_skip_reason"] = payload.get("reason", "timeout")
        sub_data["_resolved_at"] = now
        new_data[sub_state_key] = type(
            "SubStateData", (), {"fields": sub_data, "completed_at": now}
        )()

        new_conflicts = list(state.conflicts)
        new_conflicts.append(
            {
                "type": "skip",
                "node": state.current_node,
                "sub_state": sub_state_key,
                "resolved_at": now,
                "strategy": "skip",
            }
        )

        return replace(
            state,
            sub_state_data=new_data,
            conflicts=new_conflicts,
            blocked_at="",
            block_reason="",
            block_since="",
            pending_events=[],
            block_timeout_ms=0,
            updated_at=now,
        )

    @staticmethod
    def _resolve_force(
        state: WorkflowState, payload: dict[str, Any], now: str
    ) -> WorkflowState:
        """Permite el submit con advertencia."""
        sub_state_key = state.current_sub_state or payload.get("sub_state", "")
        new_data = dict(state.sub_state_data)
        sub_data = dict(
            new_data.get(sub_state_key, {}).fields if sub_state_key in new_data else {}
        )
        sub_data["_forced"] = True
        sub_data["_force_reason"] = payload.get("reason", "gate_blocked")
        sub_data["_resolved_at"] = now
        new_data[sub_state_key] = type(
            "SubStateData", (), {"fields": sub_data, "completed_at": now}
        )()

        new_conflicts = list(state.conflicts)
        new_conflicts.append(
            {
                "type": "force",
                "node": state.current_node,
                "sub_state": sub_state_key,
                "resolved_at": now,
                "strategy": "force",
            }
        )

        return replace(
            state,
            sub_state_data=new_data,
            conflicts=new_conflicts,
            blocked_at="",
            block_reason="",
            block_since="",
            pending_events=[],
            block_timeout_ms=0,
            updated_at=now,
        )

    @staticmethod
    def _resolve_wait(
        state: WorkflowState, payload: dict[str, Any], now: str
    ) -> WorkflowState:
        """Mantiene el bloqueo, registra intento."""
        new_conflicts = list(state.conflicts)
        new_conflicts.append(
            {
                "type": "wait",
                "node": state.current_node,
                "sub_state": state.current_sub_state,
                "resolved_at": now,
                "strategy": "wait",
            }
        )
        return replace(state, conflicts=new_conflicts, updated_at=now)

    @staticmethod
    def _resolve_escalate(
        state: WorkflowState,
        payload: dict[str, Any],
        now: str,
        graph: WorkflowGraph | None,
    ) -> WorkflowState:
        """Transiciona a nodo de escalación."""
        if not graph:
            raise TransitionBlockedError("Cannot escalate: WorkflowGraph missing")

        escalate_node = payload.get("escalate_node")
        if not escalate_node:
            from core.engine.domain.errors import ErrorType
            from core.engine.domain.services.escalation_registry import (
                EscalationNodeRegistry,
            )

            error_type_str = payload.get("error_type", "unknown")
            try:
                error_type = ErrorType(error_type_str)
            except ValueError:
                error_type = ErrorType.UNKNOWN
            escalate_node = EscalationNodeRegistry.get_escalation_node(error_type)

        if escalate_node not in graph.nodes:
            escalate_node = EscalationNodeRegistry.get_default_node()

        target_node = graph.nodes[escalate_node]
        target_sub = (
            target_node.first_sub_state.key if target_node.first_sub_state else "INIT"
        )
        target_idx = target_node.sub_state_index(target_sub)
        idx = target_idx if target_idx >= 0 else 0

        new_history = list(state.history)
        new_history.append(f"{escalate_node}.{target_sub} (escalate)")

        new_conflicts = list(state.conflicts)
        new_conflicts.append(
            {
                "type": "escalate",
                "from": f"{state.current_node}.{state.current_sub_state}",
                "to": f"{escalate_node}.{target_sub}",
                "resolved_at": now,
                "strategy": "escalate",
            }
        )

        return replace(
            state,
            current_node=escalate_node,
            current_sub_state=target_sub,
            sub_state_index=idx,
            history=new_history,
            conflicts=new_conflicts,
            blocked_at="",
            block_reason="",
            block_since="",
            pending_events=[],
            block_timeout_ms=0,
            updated_at=now,
        )

    @staticmethod
    def _resolve_compensate(
        state: WorkflowState, payload: dict[str, Any], now: str
    ) -> WorkflowState:
        """Ejecuta compensación y retrocede."""
        new_conflicts = list(state.conflicts)
        new_conflicts.append(
            {
                "type": "compensate",
                "node": state.current_node,
                "sub_state": state.current_sub_state,
                "resolved_at": now,
                "strategy": "compensate",
            }
        )
        return replace(
            state,
            conflicts=new_conflicts,
            blocked_at="",
            block_reason="",
            block_since="",
            pending_events=[],
            block_timeout_ms=0,
            updated_at=now,
        )


__all__ = ["BlockResolver"]
