"""
domain/services.py — Servicios de dominio puros para Workflow Navigation.

Contienen la lógica de negocio extraída de engine.py:
  - ValidationService: precondiciones, gates, datos requeridos
  - AdvanceService: avance automatico entre sub-estados, failure loops
  - TransitionService: aplicacion de transiciones de estado

PRINCIPIO: Son servicios PUROS. No hacen IO, no imprimen, no persisten.
           El caller (use case o infraestructura) decide que hacer con el resultado.
"""

from __future__ import annotations

from .models import (
    WorkflowGraph,
    SprintState,
    SubState,
    ExecutionType,
    Gate,
)
from .errors import (
    NodeNotFoundError,
)


class ValidationService:
    """Validaciones de dominio del workflow.

    SRP: Unica responsabilidad = validar reglas de negocio del workflow.
    SSOT: Toda regla de validacion vive aqui.
    """

    @staticmethod
    def check_transition_preconditions(
        state: SprintState,
        graph: WorkflowGraph,
        gate_name: str,
    ) -> list[str]:
        """Valida precondiciones para permitir una transicion inter-nodo.

        Verifica que los sub-estados obligatorios del nodo actual esten
        completos antes de transicionar a otro nodo.

        Returns:
            Lista de mensajes de error (vacia = todo ok).
        """
        errors: list[str] = []
        current_node_id = state.current_node
        node = graph.get_node(current_node_id)
        if not node:
            return [f"Nodo actual '{current_node_id}' no existe en el grafo"]

        # Verificar que sub-estados anteriores esten completos
        current_idx = state.sub_state_index
        for i, ss in enumerate(node.sub_states):
            if i > current_idx:
                break  # Solo revisar hasta el actual
            if ss.execution == ExecutionType.MANUAL and ss.required_fields:
                sub_data = state.get_sub_state_data(ss.key)
                missing = [
                    f for f in ss.required_fields if not sub_data.is_field_complete(f)
                ]
                if missing:
                    errors.append(
                        f"[{current_node_id}.{ss.key}] incomplete: "
                        f"falta {', '.join(missing)}"
                    )

        return errors

    @staticmethod
    def validate_sub_state_data(
        sub_state: SubState,
        fields: dict,
    ) -> list[str]:
        """Valida que los campos requeridos del sub-estado esten presentes.

        Returns:
            Lista de errores (vacia = valido).
        """
        errors: list[str] = []
        for field in sub_state.required_fields:
            val = fields.get(field)
            if val is None or val == "":
                errors.append(f"campo requerido '{field}' faltante o vacio")
        return errors

    @staticmethod
    def is_node_complete(state: SprintState, graph: WorkflowGraph) -> bool:
        """Determina si todos los sub-estados del nodo actual estan completos."""
        node = graph.get_node(state.current_node)
        if not node:
            return True
        return node.is_complete(state.sub_state_index)

    @staticmethod
    def can_advance(state: SprintState, graph: WorkflowGraph) -> bool:
        """Hay un sub-estado siguiente al actual?"""
        node = graph.get_node(state.current_node)
        if not node:
            return False
        return state.sub_state_index < len(node.sub_states) - 1

    @staticmethod
    def get_available_gates(
        state: SprintState,
        graph: WorkflowGraph,
    ) -> list[Gate]:
        """Obtiene las gates disponibles desde el nodo actual."""
        node = graph.get_node(state.current_node)
        if not node:
            return []
        # Solo gates inter-nodo disponibles cuando el nodo esta completo
        if ValidationService.is_node_complete(state, graph):
            return node.gates
        return []


class AdvanceService:
    """Logica de avance automatico entre sub-estados.

    SRP: Unica responsabilidad = mover el indice de sub-estado.
    No ejecuta scripts, no persiste, no imprime.
    """

    @staticmethod
    def advance(state: SprintState, graph: WorkflowGraph) -> SprintState | None:
        """Avanza al siguiente sub-estado. Retorna None si ya no hay mas."""
        node = graph.get_node(state.current_node)
        if not node:
            return None

        next_idx = state.sub_state_index + 1
        if next_idx >= len(node.sub_states):
            return None

        next_ss = node.sub_states[next_idx]
        state.sub_state_index = next_idx
        state.current_sub_state = next_ss.key
        state.add_history(f"{state.current_node}.{next_ss.key}")
        return state

    @staticmethod
    def can_advance_to(
        state: SprintState, graph: WorkflowGraph, target_key: str
    ) -> bool:
        """Verifica si se puede avanzar a un sub-estado especifico."""
        node = graph.get_node(state.current_node)
        if not node:
            return False
        target_idx = node.sub_state_index(target_key)
        if target_idx < 0:
            return False
        return target_idx == state.sub_state_index + 1

    @staticmethod
    def loop_back(
        state: SprintState,
        graph: WorkflowGraph,
        current_sub_key: str,
    ) -> SprintState | None:
        """Retrocede al sub-estado anterior (failure loop)."""
        node = graph.get_node(state.current_node)
        if not node:
            return None

        prev_idx = state.sub_state_index - 1
        if prev_idx < 0:
            return None

        prev_ss = node.sub_states[prev_idx]
        state.sub_state_index = prev_idx
        state.current_sub_state = prev_ss.key
        state.add_history(f"{state.current_node}.{prev_ss.key} (loop)")
        return state


class TransitionService:
    """Aplicacion de transiciones de estado.

    SRP: Unica responsabilidad = calcular el nuevo estado tras una transicion.
    No valida precondiciones (eso es ValidationService).
    No ejecuta scripts (eso es Infrastructure).
    """

    @staticmethod
    def apply(
        state: SprintState,
        graph: WorkflowGraph,
        target_node_id: str,
        target_sub_state: str | None = None,
    ) -> SprintState:
        """Aplica una transicion y retorna el nuevo estado.

        No muta el estado original — crea uno nuevo.
        No valida — eso es responsabilidad del caller (use case).
        """
        target_node = graph.get_node(target_node_id)
        if not target_node:
            raise NodeNotFoundError(target_node_id)

        # Resolver primer sub-estado del nodo destino
        if target_sub_state:
            first_key = target_sub_state
        elif target_node.first_sub_state:
            first_key = target_node.first_sub_state.key
        else:
            first_key = "INIT"

        # Calcular sub_state_index
        target_idx = target_node.sub_state_index(first_key)

        state.current_node = target_node_id
        state.current_sub_state = first_key
        state.sub_state_index = target_idx if target_idx >= 0 else 0
        state.add_history(f"{target_node_id}.{first_key}")

        return state
