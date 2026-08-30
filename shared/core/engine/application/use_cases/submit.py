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

class SubmitDataUseCase:
    """Caso de uso: Enviar datos para el sub-estado actual.

    Flujo enterprise (TASK-002):
    1. Carga workflow + state
    2. ContractValidator.validate_input() contra input_schema del sub-estado
    3. Persiste datos
    4. Si sub-estado tiene action, ejecuta TaskRegistry.execute(action, ctx, data)
    5. ContractValidator.validate_output() contra output_schema
    6. Ejecuta SideEffectEngine con side-effects del sub-estado
    7. Auto-advance SOLO si execution=auto y on_success
    8. Sub-estados user_input/llm_task NO ejecutan TaskRegistry
    9. Dispara eventos via EventDispatcher
    """

    def __init__(
        self,
        workflow_store: IWorkflowStore,
        actions: ActionCatalog,
        workflow_io: IWorkflowIO,
        contract_validator: type[ContractValidator] | None = None,
        event_dispatcher: Any = None,
        event_log: Any = None,
        tracer: Any = None,
        bundle: EngineBundle | None = None,
        outbox: Any = None,
    ):
        # Puertos UNIFICADOS (ADR-WALKER-067 §7.2): workflow_store (grafo+estado)
        # y actions (ActionCatalog: tasks + side-effects). El Saga de side-effects
        # lo orquesta ESTE use case vía actions (SideEffectSaga), sin ejecutor
        # infra separado en el constructor.
        self._workflow_store = workflow_store
        self._actions = actions
        self._workflow_io = workflow_io
        self._bundle = bundle
        self._contract_validator = contract_validator or ContractValidator
        self._saga = SideEffectSaga(actions=actions, tracer=tracer)
        self._event_dispatcher = event_dispatcher
        self._event_log = event_log
        self.events: list[DomainEvent] = []
        # Outbox (ADR-WALKER-067 F4/H): inyectado por el host. Si es None, el
        # saga ejecuta sin outbox (backward compat). Con outbox, cada efecto se
        # enqueue antes de ejecutar y se marca completed/failed/compensated.
        self._outbox = outbox

    def execute(
        self,
        chain_dir: str | ExecutionContext,
        fields: dict | None = None,
        task_id: str | None = None,
        ctx: ExecutionContext | None = None,
    ) -> dict:
        # Orquestacion: cada fase es un helper SRP (reduccion de cyclo).
        chain_dir, fields, task_id, ctx = self._normalize_params(
            chain_dir, fields, task_id, ctx
        )

        # ── 1. Load ────────────────────────────────────────────
        workflow = self._workflow_store.load_graph()
        state = self._workflow_store.load_state(chain_dir)
        node = workflow.get_node(state.current_node)
        if not node:
            raise ValidationError(
                f"Nodo '{state.current_node}' no existe en el workflow"
            )
        sub_state = node.find_sub_state(state.current_sub_state)
        if not sub_state:
            raise SubStateNotFoundError(state.current_node, state.current_sub_state)

        # ── 2-3. Validacion de input (con forwarding SPEC-013) ─
        target_sub_state = self._resolve_forward_target(node, sub_state, fields)
        self._validate_input(target_sub_state, fields)

        # ── 4. Persist submitted data (ELM: state es inmutable) ──
        _did_forward = target_sub_state is not sub_state
        persist_key = (
            f"{state.current_node}.{target_sub_state.key}"
            if _did_forward
            else state.current_sub_state
        )
        state = self._persist_submit(state, persist_key, fields, chain_dir, task_id)

        result = {
            "submitted": True,
            "sub_state": state.current_sub_state,
            "field_count": len(fields),
        }

        # ── 5. TaskRegistry.execute() si sub-estado tiene action ─
        task_result = self._execute_task(
            sub_state, state, chain_dir, task_id, ctx, fields, result, _did_forward
        )

        # ── 6. ContractValidator.validate_output() ──────────────
        self._validate_output(sub_state, state, fields, task_result)

        # ── 7. SideEffectEngine.execute() ──────────────────────
        state, result = self._execute_side_effects(
            sub_state, state, chain_dir, task_id, ctx, result,
            node_key=f"{node.node_id}.{state.current_sub_state}",
        )

        # ── 8. Auto-advance ─────────────────────────────────────
        # WF-7 fix: _auto_advance retorna TAMBIEN el estado resultante (que
        # pudo avanzar a on_success). Sin esto, _build_step_result recibe el
        # estado previo al avance y su save final REVIERTE la transicion.
        advanced_to, node_complete, state = self._auto_advance(
            sub_state,
            node,
            state,
            workflow,
            chain_dir,
            task_id,
            fields,
            result,
            task_result,
        )

        # ── 9b. StepResult monádico ────────────────────────────
        state, result = self._build_step_result(
            sub_state,
            state,
            chain_dir,
            fields,
            result,
            task_result,
            advanced_to,
            node_complete,
        )

        # ── 10. Dispatch events via EventDispatcher ─────────────
        _flush_events(
            self.events,
            self._event_log,
            self._event_dispatcher,
            chain_dir,
            task_id,
        )

        # ── 11. XOR-split resuelto por intent/decision ──────────
        self._resolve_xor_selection(
            sub_state, state, workflow, chain_dir, task_id, fields, result
        )
        return result

    # ── Helpers SRP de SubmitDataUseCase.execute ────────────────────────

    def _normalize_params(
        self,
        chain_dir: str | ExecutionContext,
        fields: dict | None,
        task_id: str | None,
        ctx: ExecutionContext | None,
    ) -> tuple[str, dict, str | None, ExecutionContext | None]:
        """Paso 0: normaliza parametros soportando ExecutionContext."""
        if isinstance(chain_dir, ExecutionContext):
            ctx = chain_dir
            chain_dir = ctx.chain_dir
            task_id = task_id or ctx.task_id
        elif ctx is None and fields is None and task_id is None:
            ctx = chain_dir  # type: ignore
            chain_dir = ctx.chain_dir
            task_id = ctx.task_id
            fields = {}
        if fields is None:
            fields = {}
        return chain_dir, fields, task_id, ctx

    def _resolve_forward_target(self, node: Any, sub_state: Any, fields: dict) -> Any:
        """Paso 2b: deteccion de forwarding de input (SPEC-013).

        Un sub-estado auto (ej: ANALYZE.GATHER) cuyo on_success apunta a un
        llm_task con input_schema.required (ej: ANALYZE.ANALYZE) puede recibir
        el submit con los campos del DESTINO. Si los matchean, se valida
        contra el destino y se persisten en su sub_state_data.
        """
        target_sub_state = sub_state
        if sub_state.is_auto() and sub_state.on_success:
            _target = node.find_sub_state(sub_state.on_success)
            if _target and _target.execution == ExecutionType.LLM_TASK:
                _t_in = _target.input_schema or {}
                _t_required = (
                    _t_in.get("required", []) if isinstance(_t_in, dict) else []
                )
                if _t_required and any(k in fields for k in _t_required):
                    target_sub_state = _target
        return target_sub_state

    def _validate_input(self, target_sub_state: Any, fields: dict) -> None:
        """Pasos 2-3: required fields + ContractValidator.validate_input()."""
        field_errors = ValidationService.validate_sub_state_data(
            target_sub_state, fields
        )
        if field_errors:
            raise ValidationError("Campos requeridos faltantes", field_errors)

        validate_schema = target_sub_state.input_schema or {}
        if validate_schema and self._contract_validator:
            contract = Contract.from_dict(validate_schema)
            report = self._contract_validator.verify_pre(contract, fields)
            if not report.passed:
                errors = [v.message for v in report.errors]
                raise ValidationError("Contract validation (input) failed", errors)

    def _persist_submit(
        self,
        state: WorkflowState,
        persist_key: str,
        fields: dict,
        chain_dir: str,
        task_id: str | None,
    ) -> WorkflowState:
        """Paso 4: persiste los datos sumitidos (ELM: estado inmutable)."""
        state = state.record_submit(persist_key, fields)
        if task_id:
            state = state.with_context("task_id", task_id)
        state = state.with_context("chain_dir", chain_dir)
        self._workflow_store.save_state(state, chain_dir)
        self.events.append(
            DomainEvent(
                DomainEventType.DATA_SUBMITTED,
                data={"sub_state": state.current_sub_state, "fields": fields},
            )
        )
        return state

    def _execute_task(
        self,
        sub_state: Any,
        state: WorkflowState,
        chain_dir: str,
        task_id: str | None,
        ctx: ExecutionContext | None,
        fields: dict,
        result: dict,
        _did_forward: bool,
    ) -> Any:
        """Paso 5: TaskRegistry.execute() + recarga de estado + evidencia."""
        task_result = None
        if not sub_state.action:
            return task_result
        if not self._actions:
            # FAIL-LOUD (capa_rout.md D1): si el sub-estado declara una action
            # pero el use case no recibió ActionCatalog, es un error de
            # composición, no un caso legítimo. ANTES esto era un short-circuit
            # silencioso (return task_result) que hacía que el flujo CLI/MCP
            # avanzara SIN ejecutar las actions reales (plan_sprint, spec_task,
            # verify_predicates...) — bug de producción invisible.
            logger.error(
                "Sub-estado '%s.%s' declara action='%s' pero el use case no tiene "
                "task_registry (composition root roto). Inyecta el registry via "
                "bootstrap()/resolve_service('task_registry').",
                state.current_node, sub_state.key, sub_state.action,
            )
            raise AutoAdvanceError(
                state.current_node, sub_state.key, 1,
                f"task_registry no inyectado (action='{sub_state.action}')",
            )

        exec_ctx = ctx or ExecutionContext.create(
            session_id=chain_dir.split("/")[-1],
            chain_dir=chain_dir,
            task_id=task_id or "",
        )
        # ANTI-ALUCINACIÓN (G-W6): el gate VERIFY.PREDICATE_CHECK ejecuta
        # verify_predicates SIN recibir predicates del submit (input_schema
        # vacío). El workflow.json declara que el engine lee los predicates
        # desde el DeltaGraph — aquí se cumple esa promesa: si la acción es
        # verify_predicates y el input no trae predicates, se inyectan los de
        # la spec ACTUAL del spring (SSOT). Sin esto, el gate pasaba vacío
        # como no-op (all_passed=True falso).
        if sub_state.action == "verify_predicates":
            fields = self._inject_spec_predicates(chain_dir, task_id, fields)
        if self._actions is not None:
            raw = self._actions.execute_action(sub_state.action, exec_ctx, fields)
            if raw is None:
                raise AutoAdvanceError(
                    state.current_node, sub_state.key, 1,
                    f"ActionCatalog: action '{sub_state.action}' not found",
                )
            if isinstance(raw, TaskResult):
                task_result = raw
            elif isinstance(raw, dict):
                ok = raw.get("ok", True)
                data = raw.get("data") or raw.get("result")
                error = raw.get("error") if not ok else None
                task_result = TaskResult(ok=ok, data=data, error=error)
            else:
                task_result = TaskResult.success(raw)
        else:
            task_result = self._actions.execute_action(sub_state.action, exec_ctx, fields)
        # 5a. Recargar state despues de la tarea (las tareas pueden modificar
        # el estado). EXCEPCION (SPEC-013): si hubo forwarding NO recargar.
        if not _did_forward:
            state = self._workflow_store.load_state(chain_dir)

        task_data_structured = ""
        if task_result.data:
            try:
                task_data_structured = json.dumps(task_result.data, ensure_ascii=False)[
                    :2000
                ]
            except (TypeError, ValueError):
                task_data_structured = str(task_result.data)[:1000]

        state = state.record_submit(
            state.current_sub_state,
            {
                "task_action": sub_state.action,
                "task_ok": task_result.ok,
                "task_data": task_data_structured,
            },
        )
        self._workflow_store.save_state(state, chain_dir)
        if not task_result.ok:
            error_msg = str(task_result.error) if task_result.error else "Unknown error"
            raise AutoAdvanceError(state.current_node, sub_state.key, 1, error_msg)
        result["task_executed"] = True
        result["task_action"] = sub_state.action
        result["task_result"] = str(task_result.data)[:500] if task_result.data else ""
        return task_result

    def _inject_spec_predicates(
        self,
        chain_dir: str,
        task_id: str | None,
        fields: dict,
    ) -> dict:
        """Inyecta los predicates de la spec ACTUAL desde el DeltaGraph.

        Garantía anti-alucinación (G-W6): el gate VERIFY.PREDICATE_CHECK debe
        verificar CONTRA los predicates reales de la spec, no contra lo que
        el agente decida pasar. Reglas:
          - Si el input ya trae predicates → se respeta (SIN sobrescribir).
          - Si la spec actual no tiene predicates → se devuelve el input tal
            cual: verify_predicates fallará con error explícito (ya no hay
            no-op all_passed=True vacío).
        """
        if "predicates" in fields or "predicate" in fields:
            return fields
        # El DeltaGraph/spring es INCUMBENCIA DEL HOST (ADR-WALKER-067): el
        # engine no consulta predicates de la spec. El host los inyecta en
        # `fields` antes de submit si la regla lo exige; si no vienen,
        # verify_predicates falla con error explícito (fallback documentado).
        return fields

    def _validate_output(
        self,
        sub_state: Any,
        state: WorkflowState,
        fields: dict,
        task_result: Any,
    ) -> None:
        """Paso 6: ContractValidator.validate_output() — BLOQUEANTE."""
        if not (sub_state.output_schema and self._contract_validator):
            return
        if sub_state.action:
            if task_result and isinstance(task_result.data, dict):
                output_data = task_result.data
            elif task_result and task_result.data is not None:
                raise ValidationError(
                    f"Output validation failed for {state.current_node}."
                    f"{state.current_sub_state}: task '{sub_state.action}' "
                    f"retorno tipo {type(task_result.data).__name__}, "
                    f"se esperaba dict para validar contra output_schema",
                )
            else:
                output_data = None
        else:
            output_data = fields

        if output_data is not None:
            contract = Contract.from_dict(sub_state.output_schema)
            report = self._contract_validator.verify_post(contract, output_data)
            if not report.passed:
                error_details = [v.message for v in report.errors]
                raise ValidationError(
                    f"Output contract validation failed for "
                    f"{state.current_node}.{state.current_sub_state}",
                    error_details,
                )

        # ── 6b. DTO de respuesta del sub_state `llm_task` (F3 SPEC-043) ──
        # Si el sub_state tiene DTO declarado en engine/workflow/contracts.py,
        # se valida TAMBIEN la estructura tipada (from_dict). El error se
        # propaga como ValidationError → SchemaViolation → block+message
        # (mismo canal 422/detalle que el paso 6).
        if sub_state.is_llm_task():
            try:
                from data.workflow.contracts import (
                    get_workflow_output_dto,
                )

                dto_cls = get_workflow_output_dto(
                    state.current_node, state.current_sub_state
                )
                if dto_cls is not None and output_data is not None:
                    dto_cls.from_dict(output_data)
            except ValidationError:
                raise
            except ValueError as e:
                raise ValidationError(
                    f"Output contract DTO validation failed for "
                    f"{state.current_node}.{state.current_sub_state}",
                    [str(e)],
                )

    def _execute_side_effects(
        self,
        sub_state: Any,
        state: WorkflowState,
        chain_dir: str,
        task_id: str | None,
        ctx: ExecutionContext | None,
        result: dict,
        node_key: str = "",
    ) -> tuple[WorkflowState, dict]:
        """Paso 7: SideEffectSaga (vía ActionCatalog) + recarga de estado."""
        if not (sub_state.side_effects and self._saga):
            return state, result
        exec_ctx = ctx or ExecutionContext.create(
            session_id=chain_dir.split("/")[-1],
            chain_dir=chain_dir,
            task_id=task_id or "",
        )
        if node_key:
            exec_ctx.baggage["node_key"] = node_key
        side_results = self._saga.run(
            exec_ctx, sub_state.side_effects, outbox=self._outbox
        )
        # SPEC-014 F2 — política de fallo unificada (cierra la asimetría):
        # un side effect OBLIGATORIO (optional=False) que falla se ELEVA como
        # SideEffectFailedError (fail-loud) en vez de compensar y seguir.
        # El ErrorHandler lo clasifica como TaskFailedError vía bridge → misma
        # estrategia por nodo, mismo on_failure. Los optional=True siguen con
        # warning y continuar (sin cambio).
        failed_mandatory = [
            r for r in side_results if not r.get("ok") and not r.get("optional")
        ]
        if failed_mandatory:
            raise SideEffectFailedError(
                failed_mandatory[0]["type"],
                "Side effect(s) obligatorio(s) fallaron: "
                + ", ".join(r["type"] for r in failed_mandatory),
            )
        # 7a. Recargar state despues de side-effects (modifican estado)
        state = self._workflow_store.load_state(chain_dir)
        state = state.record_submit(
            state.current_sub_state,
            {"side_effects": side_results},
        )
        self._workflow_store.save_state(state, chain_dir)
        result["side_effects"] = side_results
        return state, result

    def _auto_advance(
        self,
        sub_state: Any,
        node: Any,
        state: WorkflowState,
        workflow: WorkflowGraph,
        chain_dir: str,
        task_id: str | None,
        fields: dict,
        result: dict,
        task_result: Any,
    ) -> tuple[str, bool, WorkflowState]:
        """Paso 9: auto-advance a on_success (con guard de llm_task).

        Retorna (advanced_to, node_complete, state): el estado de retorno es
        el RESULTANTE del avance (ya guardado), para que el caller persista
        sobre el estado avanzado y no sobre el previo (fix WF-7).
        """
        task_success = task_result is not None and task_result.ok
        should_advance = sub_state.on_success and (
            sub_state.is_auto()
            or (
                sub_state.execution == ExecutionType.LLM_TASK
                and (task_success or not sub_state.action)
            )
        )
        advanced_to = ""
        node_complete = False
        if not should_advance:
            node_complete = ValidationService.is_node_complete(state, workflow)
            result["node_complete"] = node_complete
            return advanced_to, node_complete, state

        advanced = AdvanceService.advance(state, workflow)
        if advanced:
            # Guard estructural: no auto-avanzar a un llm_task que requiere
            # input si el submit actual no lo provee (evita REFINE en loop).
            next_node = (
                workflow.get_node(advanced.current_node)
                if hasattr(advanced, "current_node")
                else None
            )
            next_ss = (
                next_node.find_sub_state(advanced.current_sub_state)
                if next_node
                else None
            )
            if next_ss and next_ss.execution == ExecutionType.LLM_TASK:
                next_input = next_ss.input_schema or {}
                next_required = (
                    next_input.get("required", [])
                    if isinstance(next_input, dict)
                    else []
                )
                if next_required and not fields:
                    self._workflow_store.save_state(state, chain_dir)
                    result["advanced_to"] = state.current_sub_state
                    result["node_complete"] = False
                    result["waiting_for_input"] = True
                    result["message"] = (
                        f"{advanced.current_node}.{advanced.current_sub_state} "
                        f"requiere input ({', '.join(next_required)}) — "
                        f"completa los datos antes de avanzar."
                    )
                    return advanced_to, node_complete, state

        if advanced:
            # Promover provides del sub-estado completado al contexto global
            from .context_resolver import ContextResolver

            state = ContextResolver().promote_provides(advanced, node, sub_state)
            self._workflow_store.save_state(state, chain_dir)
            self.events.append(
                DomainEvent(
                    DomainEventType.STATE_TRANSITIONED,
                    data={"from": sub_state.key, "to": state.current_sub_state},
                )
            )
            result["advanced_to"] = state.current_sub_state
            advanced_to = state.current_sub_state
            node_complete = ValidationService.is_node_complete(state, workflow)
            result["node_complete"] = node_complete
        else:
            node_complete = True
            result["node_complete"] = True
        return advanced_to, node_complete, state

    def _build_step_result(
        self,
        sub_state: Any,
        state: WorkflowState,
        chain_dir: str,
        fields: dict,
        result: dict,
        task_result: Any,
        advanced_to: str,
        node_complete: bool,
    ) -> tuple[WorkflowState, dict]:
        """Paso 9b: StepResult monádico + persistencia tipada."""
        from ...domain.step_result import StepResult

        task_success = task_result is not None and task_result.ok
        side_results = result.get("side_effects", []) or []
        step_result = StepResult(
            ok=task_success if sub_state.action else True,
            task=task_result,
            side_effects=side_results,
            advanced_to=advanced_to,
            node_complete=node_complete,
            sub_state=sub_state.key,
            fields=fields,
            error=result.get("error", ""),
        )
        result["step_result"] = step_result
        result["prompt_placeholders"] = step_result.to_prompt_dict()

        state = state.record_submit(
            sub_state.key,
            {
                "step_checkpoint": step_result.to_checkpoint(),
                "prompt_placeholders": step_result.to_prompt_dict(),
            },
        )
        self._workflow_store.save_state(state, chain_dir)
        return state, result

    def _resolve_xor_selection(
        self,
        sub_state: Any,
        state: WorkflowState,
        workflow: WorkflowGraph,
        chain_dir: str,
        task_id: str | None,
        fields: dict,
        result: dict,
    ) -> None:
        """Paso 11: XOR-split resuelto por intent/decision."""
        xor_selector = ""
        gate_name = ""
        if fields:
            if fields.get("intent"):
                xor_selector = str(fields["intent"])
                gate_name = xor_selector
            elif fields.get("decision"):
                gate_name = _resolve_decision_gate(
                    state, workflow, str(fields["decision"]), fields
                )
                if gate_name:
                    xor_selector = gate_name

        if xor_selector:
            graph_type = ValidationService.classify_sub_state(state, workflow)
            if graph_type in ("xor", "guarded"):
                gate = ValidationService.resolve_xor_by_intent(
                    state, workflow, xor_selector
                )
                if gate:
                    result["xor_resolved"] = True
                    result["xor_gate"] = gate_name
                    result["xor_next"] = (
                        f"{gate.target_node}.{gate.target_sub_state or '*'}"
                    )
                    result["_auto_transition"] = True
                    result["_gate"] = gate_name
        elif fields and fields.get("decision") and sub_state.on_success:
            self._advance_decision_no_gate(
                state, workflow, sub_state, chain_dir, task_id, result
            )

    def _advance_decision_no_gate(
        self,
        state: WorkflowState,
        workflow: WorkflowGraph,
        sub_state: Any,
        chain_dir: str,
        task_id: str | None,
        result: dict,
    ) -> None:
        """Paso 11b: decision sin gate -> avance por on_success.

        Un user_input que recibe decision pero cuyo override NO mapea a una
        gate (ej: CONFIRM_GATE.approve -> '' para que ROUTE resuelva el
        target dinamicamente) avanza por on_success. Sin esto, la decision
        se capturaba pero el sub-estado quedaba bloqueado en user_input.
        """
        try:
            advanced = AdvanceService.advance(state, workflow)
            if advanced:
                self._workflow_store.save_state(advanced, chain_dir)
                result["advanced_to"] = advanced.current_sub_state
                result["_on_success_advance"] = True
                self.events.append(
                    DomainEvent(
                        DomainEventType.STATE_TRANSITIONED,
                        data={
                            "from": sub_state.key,
                            "to": advanced.current_sub_state,
                        },
                    )
                )
        except Exception:
            pass

    def pop_events(self) -> list[DomainEvent]:
        events = list(self.events)
        self.events.clear()
        return events


