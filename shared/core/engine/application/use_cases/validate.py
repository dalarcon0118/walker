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

class ValidateCurrentStateUseCase:
    """Caso de uso: Validar el estado actual contra el workflow.

    1. Carga workflow + state
    2. Ejecuta validaciones de dominio
    3. Retorna resultado
    """

    def __init__(
        self,
        workflow_store: IWorkflowStore,
    ):
        self._workflow_store = workflow_store

    def execute(self, chain_dir: str) -> dict:
        workflow = self._workflow_store.load_graph()
        state = self._workflow_store.load_state(chain_dir)
        return self._validate(workflow, state)

    def _validate(self, workflow: WorkflowGraph, state: WorkflowState) -> dict:
        errors = []
        node = workflow.get_node(state.current_node)
        if not node:
            errors.append(
                {
                    "path": "current_node",
                    "msg": f"nodo '{state.current_node}' no existe",
                }
            )

        sub_state = node.find_sub_state(state.current_sub_state) if node else None
        if node and not sub_state:
            errors.append(
                {
                    "path": "current_sub_state",
                    "msg": f"sub-state '{state.current_sub_state}' no existe en '{state.current_node}'",
                }
            )

        if sub_state and RequiredFieldResolver.derive(sub_state):
            data = state.get_sub_state_data(state.current_sub_state)
            field_errors = ValidationService.validate_sub_state_data(
                sub_state, data.fields
            )
            for fe in field_errors:
                errors.append(
                    {
                        "path": f"sub_state_data.{state.current_sub_state}.{fe}",
                        "msg": fe,
                    }
                )

        return {"ok": len(errors) == 0, "errors": errors, "state": state}


