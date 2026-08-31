"""
application/use_cases.py — Casos de uso del Workflow Navigation.

Cada caso de uso es una operacion completa que orquesta:
  1. Domain services (logica pura)
  2. Infrastructure (IO)
  3. Emite eventos de dominio

SRP: Cada caso de uso tiene EXACTAMENTE UNA responsabilidad.
SSOT: Delega en domain/models.py y domain/services.py para las reglas.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ...domain.models import (
    WorkflowGraph,
    WorkflowState,
    DomainEvent,
    Gate,
    ExecutionContext,
    ExecutionType,
    TaskResult,
)
from ...domain.enums import DomainEventType
from ...domain.services import (
    ValidationService,
    AdvanceService,
    TransitionService,
    RequiredFieldResolver,
)
from ...domain.errors import (
    TransitionBlockedError,
    GateValidationError,
    ValidationError,
    SubStateNotFoundError,
    AutoAdvanceError,
    SideEffectFailedError,
)
from ...domain.workflow_action import ActionCatalog
from shared.core.engine.domain.ports import IWorkflowIO, IWorkflowStore
from ..bundle import EngineBundle
from ..side_effect_saga import SideEffectSaga
from shared.core.engine.domain.contract_validator import ContractValidator, Contract

logger = logging.getLogger(__name__)

from ._helpers import (
    _flush_events,
    _sprint_id_of,
    _resolve_decision_gate,
    _DECISION_GATE_OVERRIDES,
    _sub_state_decision_prefix,
)

class AdvanceSubStateUseCase:
    """Caso de uso: Avanzar al siguiente sub-estado dentro del mismo nodo.

    No requiere gate. Usa on_success del sub-estado actual.
    Diseñado para user_input después de que el agente presente resultados.
    """

    def __init__(
        self,
        workflow_store: IWorkflowStore,
    ):
        self._workflow_store = workflow_store

    def execute(self, chain_dir: str | ExecutionContext) -> dict:
        if isinstance(chain_dir, ExecutionContext):
            chain_dir = chain_dir.chain_dir

        workflow = self._workflow_store.load_graph()
        state = self._workflow_store.load_state(chain_dir)
        node = workflow.get_node(state.current_node)
        if not node:
            return {"ok": False, "error": f"Nodo '{state.current_node}' no encontrado"}

        sub_state = node.find_sub_state(state.current_sub_state)
        if not sub_state:
            return {
                "ok": False,
                "error": f"Sub-estado '{state.current_sub_state}' no encontrado",
            }

        if not sub_state.on_success:
            return {
                "ok": False,
                "error": "Sub-estado actual no tiene on_success definido",
            }

        advanced = AdvanceService.advance(state, workflow)
        if not advanced:
            return {"ok": False, "error": "No hay mas sub-estados para avanzar"}

        self._workflow_store.save_state(state, chain_dir)
        return {
            "ok": True,
            "from": sub_state.key,
            "to": state.current_sub_state,
            "state": state,
        }


