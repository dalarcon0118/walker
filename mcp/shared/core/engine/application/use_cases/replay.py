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

class ReplayUseCase:
    """Caso de uso: Reconstruir estado desde event log.

    SRP: Unica responsabilidad = replay de eventos.
    """

    def __init__(self, workflow_store: IWorkflowStore):
        self._workflow_store = workflow_store

    def execute(self, chain_dir: str, event_log: list[dict]) -> WorkflowState:
        """Reconstruye estado aplicando eventos secuencialmente."""
        state = WorkflowState()
        for event in event_log:
            et = event.get("type", "")
            data = event.get("data", {})
            if et == "state_transitioned":
                # Construir grafo minimo para la transicion
                # (en produccion esto vendria del workflow_repo)
                pass
            elif et == "data_submitted":
                sub_key = data.get("sub_state", "")
                fields = data.get("fields", {})
                state.record_submit(sub_key, fields)
        return state


# ── Sprint status check (SSOT: solo engine conoce el workflow state) ──────


