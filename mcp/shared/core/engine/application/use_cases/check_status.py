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

class CheckSprintStatusUseCase:
    """Check sprint status: current node, sub-state, available gates.

    SRP: Single source of truth for sprint/workflow status.
         No other feature module should read estado files directly.
    """

    @staticmethod
    def execute(chain_dir: str, project_id: str) -> dict:
        """Check sprint status.

        Args:
            chain_dir: Path to the sprint chain directory.
            project_id: Project identifier.

        Returns:
            dict with sprint status, or None if no active sprint.
        """
        if not chain_dir:
            return {"active": False, "node": "", "sub_state": "", "gates": []}

        from .workflow_api import WorkflowAPI

        try:
            api = WorkflowAPI(bundle=self._bundle, chain_dir=chain_dir, project_id=project_id)
            api.ensure_session()
            sprint_status = api.status()
            gates = sprint_status.get("available_gates", [])
            return {
                "active": True,
                "node": sprint_status.get("node", "?"),
                "sub_state": sprint_status.get("sub_state", "?"),
                "history": sprint_status.get("history", []),
                "gates": gates,
                "prompt": sprint_status.get("prompt", ""),
                "prompt_id": sprint_status.get("prompt_id", ""),
                "required_fields": sprint_status.get("required_fields", []),
                "execution_type": sprint_status.get("execution_type", ""),
                "graph_progress": sprint_status.get("graph_progress", {}),
                # "informal" (spring informal del delta) es INCUMBENCIA DEL HOST
                # (ADR-WALKER-067): el engine no conoce ese concepto. El host lo
                # computa y lo agrega al status si lo necesita.
                "sprint_id": _sprint_id_of(chain_dir),
            }
        except Exception as e:
            return {
                "active": False,
                "node": "",
                "sub_state": "",
                "gates": [],
                "error": str(e),
            }


# ── Helpers ────────────────────────────────────────────────────────────────


def _sub_state_decision_prefix(sub_state_key: str) -> str:
    """Deriva el prefijo de la gate de decision para un sub-estado user_input.

    Convencion declarativa: un sub-estado con decision binaria
    (approve/revise) expone gates XOR de la forma
    '<prefix>_approved' y '<prefix>_revised'. El prefijo es el nombre
    del sub-estado en snake_case.

    Ejemplos:
        PLAN_APPROVAL  -> plan_approval (gates plan_approval_approved/revised)
        CLOSE_CONFIRM  -> close_confirm_approved / close_confirm_revised
        TASK_REVIEW    -> task_review_approved / task_review_revised
    """
    import re

    s = sub_state_key or ""
    # CamelCase/SCREAMING -> snake_case: PLAN_APPROVAL -> plan_approval
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = s.replace("-", "_")
    return s.lower()


def _sprint_id_of(chain_dir: str) -> str:
    """Deriva el spring_id del chain_dir (nombre del directorio)."""
    import os

    return os.path.basename(os.path.normpath(chain_dir or ""))


# Mapa de decisiones ESPECIFICAS por sub-estado (override de la convencion).
# Un sub-estado puede declarar aqui como mapea cada decision a su gate.
# Las gates de aprobacion de cada nodo tienen nombres propios (design_approved,
# analysis_approved, task_complete) que NO se derivan por la convencion
# <prefix>_approved — por eso se declaran explicitamente aqui.
_DECISION_GATE_OVERRIDES: dict[str, dict[str, str]] = {
    # TASK_REVIEW: approve -> next_task (o all_done si es la ultima), refine -> refine_task
    "TASK_REVIEW": {
        "approve": "next_task",
        "refine": "refine_task",
        "all_done": "all_done",
    },
    # DESIGN_REVIEW: approve -> design_approved (gate propia del nodo DESIGN)
    "DESIGN_REVIEW": {
        "approve": "design_approved",
        "reject": "design_needs_refine",
        "refine": "design_needs_refine",
    },
    # ANALYZE.REVIEW: approve -> analysis_approved (KNOWLEDGE_REVIEW fue
    # fusionado en REVIEW — SPEC-005)
    "REVIEW": {
        "approve": "analysis_approved",
        "reject": "needs_refine",
        "refine": "needs_refine",
    },
    # IMPLEMENT gates: user gates de implementacion
    "USER_GATE": {
        "approve": "implementation_done",
        "reject": "needs_refine",
        "cancel": "cancel",
    },
    "CODE_REVIEW_RESULT": {
        "approve": "implementation_done",
        "reject": "needs_refine",
        "refine": "needs_refine",
    },
    "REPORT_GATE": {
        "approve": "implementation_done",
        "reject": "needs_refine",
        "refine": "needs_refine",
    },
    # VERIFY gates: QA y PO_ACCEPT -> task_complete
    "QA": {
        "approve": "task_complete",
        "reject": "needs_refine",
        "refine": "needs_refine",
    },
    "PO_ACCEPT": {
        "approve": "task_complete",
        "reject": "needs_refine",
        "refine": "needs_refine",
    },
    # CLOSE_CONFIRM: close_sprint
    "CLOSE_CONFIRM": {
        "approve": "close_sprint",
        "reject": "back_to_active",
    },
    # CONFIRM_GATE (REFINE): approve -> dejar que ROUTE (auto) resuelva el
    # target del diagnostico dinamicamente (NO mapear a una gate fija);
    # modify -> volver a DIAGNOSE, cancel -> volver a sprint.
    "CONFIRM_GATE": {
        "approve": "",
        "modify": "reroute_design",
        "cancel": "reroute_sprint",
    },
}


def _resolve_decision_gate(
    state: WorkflowState,
    workflow: WorkflowGraph,
    decision: str,
    fields: dict,
) -> str:
    """Mapea una decision de user_input a la gate XOR a disparar.

    Orden de resolucion:
      1. Override declarativo por sub-estado (_DECISION_GATE_OVERRIDES):
         - TASK_REVIEW.approve -> next_task (o all_done si es la ultima tarea)
         - TASK_REVIEW.refine  -> refine_task
      2. Convencion binaria generica: approve/confirm/yes/proceed/accept
         -> <prefix>_approved; revise/reject/no/back/cancel -> <prefix>_revised.

    Args:
        state: Estado del workflow (sub-estado actual).
        workflow: Grafo del workflow (para contar specs restantes).
        decision: Valor del campo decision (approve, revise, refine, ...).
        fields: Campos completos del submit (para contexto adicional).

    Returns:
        Nombre de la gate a disparar, o "" si no hay mapeo.
    """
    decision = (decision or "").lower().strip()
    sub_key = state.current_sub_state
    if not decision:
        return ""

    # 1. Override declarativo
    override = _DECISION_GATE_OVERRIDES.get(sub_key)
    if override and decision in override:
        gate = override[decision]
        # TASK_REVIEW.approve: si es la ultima tarea, all_done en vez de next_task
        if sub_key == "TASK_REVIEW" and decision == "approve":
            total = 0
            current = 0
            # El conteo de specs (all_done en TASK_REVIEW) depende del
            # DeltaGraph, que es INCUMBENCIA DEL HOST (ADR-WALKER-067): el
            # engine NO consulta spring. El host provee total/current en
            # state.context.spring; si no están, se conserva next_task.
            try:
                ctx_state = state.context.get("spring", {}) or {}
                total = int(ctx_state.get("total_specs", 0) or 0)
                current = int(ctx_state.get("current_spec_index", 0) or 0)
            except Exception:
                pass
            if total > 0 and current >= total - 1:
                gate = "all_done"
        return gate

    # 2. Convencion binaria generica
    prefix = _sub_state_decision_prefix(sub_key)
    positive = {"approve", "confirm", "yes", "proceed", "accept"}
    negative = {"revise", "reject", "no", "back", "cancel"}
    if decision in positive:
        return f"{prefix}_approved"
    if decision in negative:
        return f"{prefix}_revised"
    return ""

