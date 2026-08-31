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

class TransitionUseCase:
    """Caso de uso: Transicionar entre nodos del workflow.

    Flujo:
    1. Carga workflow + state
    2. Busca la gate solicitada
    3. Valida precondiciones
    4. Ejecuta validacion de gate (script opcional)
    5. Aplica la transicion
    6. Persiste
    7. Retorna el nuevo estado
    8. Dispara eventos via EventDispatcher si hay handlers registrados
    """

    def __init__(
        self,
        workflow_store: IWorkflowStore,
        workflow_io: IWorkflowIO,
        event_dispatcher: Any = None,
        event_log: Any = None,
        bundle: EngineBundle | None = None,
    ):
        self._workflow_store = workflow_store
        self._workflow_io = workflow_io
        self._event_dispatcher = event_dispatcher
        self._event_log = event_log
        self._bundle = bundle
        self.events: list[DomainEvent] = []

    def execute(
        self,
        chain_dir: str | ExecutionContext,
        gate_name: str = "",
        task_id: str | None = None,
        reason: str = "",
        skip_preconditions: bool = False,
        ctx: ExecutionContext | None = None,
    ) -> dict:
        # ── 0. Normalizar parametros: soportar ExecutionContext ──
        if isinstance(chain_dir, ExecutionContext):
            ctx = chain_dir
            chain_dir = ctx.chain_dir
            task_id = task_id or ctx.task_id
        if ctx:
            chain_dir = ctx.chain_dir
            task_id = task_id or ctx.task_id
        # ── 1. Load ────────────────────────────────────────────
        workflow = self._workflow_store.load_graph()
        state = self._workflow_store.load_state(chain_dir)

        # ── 2. Find gate ────────────────────────────────────────
        gate = self._find_gate(
            workflow, state.current_node, gate_name, state.current_sub_state
        )
        if not gate:
            raise ValidationError(
                f"Gate '{gate_name}' no disponible desde '{state.current_node}.{state.current_sub_state}'"
            )

        # ── 3. Validate preconditions ───────────────────────────
        if not skip_preconditions:
            pre_errors = ValidationService.check_transition_preconditions(
                state, workflow, gate_name
            )
            if pre_errors:
                raise TransitionBlockedError(
                    f"Precondiciones no cumplidas para gate '{gate_name}'", pre_errors
                )

        # ── 3b. Evaluate contract.pre / invariants (ANTES de transicionar) ──
        if gate.contract:
            from ...domain.contracts import ContractEvaluator

            pre_errors = ContractEvaluator.evaluate_pre(state, gate.contract)
            if pre_errors:
                raise TransitionBlockedError(
                    f"Gate '{gate_name}' contract.pre bloqueado", pre_errors
                )

        # ── 4. Execute gate validation script ───────────────────
        if gate.gate_validation_script:
            vresult = self._workflow_io.run_script(
                gate.gate_validation_script, chain_dir, task_id
            )
            if vresult["exit_code"] not in gate.gate_valid_exit_codes:
                raise GateValidationError(gate_name, vresult.get("stderr", ""))
            self.events.append(
                DomainEvent(
                    DomainEventType.GATE_VALIDATED,
                    data={"gate": gate_name, "script": gate.gate_validation_script},
                )
            )

        # ── 5. Capturar estado origen ANTES de aplicar transicion ──
        source_node_id = state.current_node
        source_node = workflow.get_node(source_node_id)

        # ── 6. Apply transition (inmutable: nuevo estado) ─────────
        state = TransitionService.apply(
            state, workflow, gate.target_node, gate.target_sub_state
        )
        if reason:
            state = state.with_context("last_reason", reason)
        if task_id:
            state = state.with_context("task_id", task_id)
        state = state.with_context("chain_dir", chain_dir)

        # ── 6b. Promover provides del nodo origen al global state ──
        if source_node and source_node.provides:
            from ..context_resolver import ContextResolver

            state = ContextResolver().promote_provides(state, source_node)

        # ── 6b. Aplicar context_updates de la edge ────────────────
        if gate.context_updates:
            from ..context_resolver import ContextResolver

            cr = ContextResolver()
            state = cr.apply_context_updates(state, gate.context_updates)

        # ── 6c. Sincronizar SessionRecord con contexto promovido ──
        if state.context.get("project_id"):
            try:
                from ...domain.ports_registry import resolve_port
                from ...domain.ports import ISessionStore

                store = resolve_port(ISessionStore)
                record = store.get_active() if store else None
                if record:
                    record.project_id = state.context["project_id"]
                    record.chain_dir = chain_dir
                    store.save(record)
            except Exception:
                pass

        # ── 6d. Evaluate contract.post DESPUES de transicionar ──
        # El post describe una condicion del ESTADO DESTINO (nuevo estado).
        # Solo puede verificarse tras aplicar la transicion, no antes.
        if gate.contract:
            from ...domain.contracts import ContractEvaluator

            post_errors = ContractEvaluator.evaluate_post(state, gate.contract)
            if post_errors:
                raise TransitionBlockedError(
                    f"Gate '{gate_name}' contract.post bloqueado", post_errors
                )

        self._workflow_store.save_state(state, chain_dir)
        self.events.append(
            DomainEvent(
                DomainEventType.STATE_TRANSITIONED,
                data={
                    "gate": gate_name,
                    "from_node": state.current_node,  # ya actualizado
                    "to_node": gate.target_node,
                    "reason": reason,
                },
            )
        )

        # ── 6. Dispatch events via EventDispatcher ──────────────
        _flush_events(
            self.events,
            self._event_log,
            self._event_dispatcher,
            chain_dir,
            task_id,
        )

        return {
            "ok": True,
            "from_node": state.current_node,
            "to_node": gate.target_node,
            "to_sub_state": state.current_sub_state,
            "gate": gate_name,
            "state": state,
        }

    @staticmethod
    def _find_gate(
        graph: WorkflowGraph,
        node_id: str,
        gate_name: str,
        current_sub_state: str = "",
    ) -> Gate | None:
        """Busca gate en el nodo o en gates globales.

        Valida tambien el sub-estado origen: si la edge declara
        source_sub_state (from_sub), solo es valida desde ese sub-estado.
        """
        gate = graph.find_gate(node_id, gate_name)
        if not gate:
            return None
        # Si el gate no declara from_sub, aplica a cualquier sub-estado.
        if gate.source_sub_state and gate.source_sub_state != current_sub_state:
            return None
        return gate

    def pop_events(self) -> list[DomainEvent]:
        events = list(self.events)
        self.events.clear()
        return events


