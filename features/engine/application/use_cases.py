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

from ..domain.models import (
    WorkflowGraph,
    WorkflowState,
    DomainEvent,
    Gate,
    ExecutionContext,
    ExecutionType,
)
from ..domain.enums import DomainEventType
from ..domain.registry import TaskRegistry
from ..domain.services import (
    ValidationService,
    AdvanceService,
    TransitionService,
    RequiredFieldResolver,
)
from ..domain.errors import (
    TransitionBlockedError,
    GateValidationError,
    ValidationError,
    SubStateNotFoundError,
    AutoAdvanceError,
)
from ..infrastructure.repositories import (
    WorkflowRepository,
    WorkflowStateRepository,
    ScriptRunner,
)
from ..infrastructure.side_effects import SideEffectEngine
from features.engine.domain.contract_validator import ContractValidator, Contract

logger = logging.getLogger(__name__)


# ── Shared helper ──────────────────────────────────────────────────────────


def _flush_events(
    events: list[DomainEvent],
    event_log: Any,
    event_dispatcher: Any,
    chain_dir: str | None,
    task_id: str | None,
) -> None:
    """Persiste y dispara eventos acumulados.

    SRP: Unica funcion compartida para persistir eventos en EventLog
    y dispararlos via EventDispatcher. DRY: evita duplicar en cada UseCase.

    Nota: event_log/event_dispatcher tipados como Any para evitar
    import circular (events.py <- workflow_engine -> use_cases.py -> events.py).
    """
    if event_log and chain_dir:
        event_log.set_chain_dir(chain_dir)
        for event in events:
            event_log.append_and_flush(event)
    if event_dispatcher:
        event_dispatcher.dispatch_many(events, chain_dir, task_id)


class ValidateCurrentStateUseCase:
    """Caso de uso: Validar el estado actual contra el workflow.

    1. Carga workflow + state
    2. Ejecuta validaciones de dominio
    3. Retorna resultado
    """

    def __init__(
        self,
        workflow_repo: WorkflowRepository,
        state_repo: WorkflowStateRepository,
    ):
        self._workflow_repo = workflow_repo
        self._state_repo = state_repo

    def execute(self, chain_dir: str) -> dict:
        workflow = self._workflow_repo.load(chain_dir)
        state = self._state_repo.load(chain_dir)
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
        workflow_repo: WorkflowRepository,
        state_repo: WorkflowStateRepository,
        script_runner: ScriptRunner,
        task_registry: TaskRegistry | None = None,
        contract_validator: type[ContractValidator] | None = None,
        side_effect_engine: SideEffectEngine | None = None,
        event_dispatcher: Any = None,
        event_log: Any = None,
        tracer: Any = None,
    ):
        self._workflow_repo = workflow_repo
        self._state_repo = state_repo
        self._script_runner = script_runner
        self._task_registry = task_registry
        self._contract_validator = contract_validator or ContractValidator
        self._side_effect_engine = side_effect_engine or SideEffectEngine(
            tracer=tracer,
        )
        self._event_dispatcher = event_dispatcher
        self._event_log = event_log
        self.events: list[DomainEvent] = []

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
        workflow = self._workflow_repo.load(chain_dir)
        state = self._state_repo.load(chain_dir)
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

        # ── 8. Auto-execute legacy script si mode=auto ──────────
        auto_early = self._run_auto_script(
            sub_state, state, workflow, chain_dir, task_id, result
        )
        if auto_early:
            return auto_early

        # ── 9. Auto-advance ─────────────────────────────────────
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
        self._state_repo.save(chain_dir, state)
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
        if not self._task_registry:
            # FAIL-LOUD (capa_rout.md D1): si el sub-estado declara una action
            # pero el use case no recibió TaskRegistry, es un error de
            # composición, no un caso legítimo. ANTES esto era un short-circuit
            # silencioso (return task_result) que hacía que el flujo CLI/MCP
            # avanzara SIN ejecutar las actions reales (plan_sprint, spec_task,
            # verify_predicates...) — bug de producción invisible.
            logger.error(
                "Sub-estado '%s.%s' declara action='%s' pero el use case no tiene "
                "task_registry (composition root roto). Inyecta el registry via "
                "bootstrap()/get_module_service('task_registry').",
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
        task_result = self._task_registry.execute(sub_state.action, exec_ctx, fields)
        # 5a. Recargar state despues de la tarea (las tareas pueden modificar
        # el estado). EXCEPCION (SPEC-013): si hubo forwarding NO recargar.
        if not _did_forward:
            state = self._state_repo.load(chain_dir)

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
        self._state_repo.save(chain_dir, state)
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
        try:
            from features.delta_tracking import spring_service

            svc = spring_service(chain_dir=chain_dir)
            if svc is None:
                return fields
            spring_id = svc.get_spring_id(chain_dir)
            spec = svc.get_current_spec(spring_id) if spring_id else None
            if not spec:
                return fields
            preds = spec.get("validation", {}).get("predicates", []) or spec.get(
                "predicates", []
            )
            if not preds and task_id:
                # Fallback: buscar la spec por task_id explícito
                try:
                    spec_by_task = svc.get_spec(spring_id, task_id)
                    if spec_by_task:
                        preds = spec_by_task.get("validation", {}).get(
                            "predicates", []
                        ) or spec_by_task.get("predicates", [])
                except Exception:
                    preds = []
            enriched = dict(fields)
            if preds:
                enriched["predicates"] = preds
            return enriched
        except Exception:
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
                from features.engine.workflow.contracts import (
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
        """Paso 7: SideEffectEngine.execute() + recarga de estado."""
        if not (sub_state.side_effects and self._side_effect_engine):
            return state, result
        exec_ctx = ctx or ExecutionContext.create(
            session_id=chain_dir.split("/")[-1],
            chain_dir=chain_dir,
            task_id=task_id or "",
        )
        if node_key:
            exec_ctx.baggage["node_key"] = node_key
        side_results = self._side_effect_engine.execute(
            exec_ctx, sub_state.side_effects
        )
        # 7a. Recargar state despues de side-effects (modifican estado)
        state = self._state_repo.load(chain_dir)
        state = state.record_submit(
            state.current_sub_state,
            {"side_effects": side_results},
        )
        self._state_repo.save(chain_dir, state)
        result["side_effects"] = side_results
        return state, result

    def _run_auto_script(
        self,
        sub_state: Any,
        state: WorkflowState,
        workflow: WorkflowGraph,
        chain_dir: str,
        task_id: str | None,
        result: dict,
    ) -> dict | None:
        """Paso 8: auto-execute de script legacy (backward compat)."""
        if not (sub_state.is_auto() and sub_state.auto_complete_script):
            return None
        auto_result = self._script_runner.run(
            sub_state.auto_complete_script, chain_dir, task_id
        )
        state = state.record_submit(
            state.current_sub_state,
            {
                "auto_script": sub_state.auto_complete_script,
                "exit_code": auto_result["exit_code"],
                "stdout": (auto_result.get("stdout", "") or "")[:2000],
                "stderr": (auto_result.get("stderr", "") or "")[:1000],
            },
        )
        self._state_repo.save(chain_dir, state)
        if auto_result["exit_code"] != 0:
            if sub_state.on_failure_loop:
                looped = AdvanceService.loop_back(state, workflow, sub_state.key)
                if looped:
                    state = looped
                    self._state_repo.save(chain_dir, state)
                self.events.append(
                    DomainEvent(
                        DomainEventType.FAILURE_LOOPED,
                        data={
                            "sub_state": sub_state.key,
                            "exit_code": auto_result["exit_code"],
                        },
                    )
                )
                _flush_events(
                    self.events,
                    self._event_log,
                    self._event_dispatcher,
                    chain_dir,
                    task_id,
                )
                result["looped_back"] = True
                result["auto_result"] = auto_result
                return result
            else:
                raise AutoAdvanceError(
                    state.current_node,
                    sub_state.key,
                    auto_result["exit_code"],
                    auto_result.get("stderr", ""),
                )
        self.events.append(
            DomainEvent(
                DomainEventType.AUTO_ADVANCED,
                data={
                    "sub_state": sub_state.key,
                    "script": sub_state.auto_complete_script,
                    "exit_code": auto_result["exit_code"],
                },
            )
        )
        result["auto_executed"] = True
        result["auto_result"] = auto_result
        return None

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
                    self._state_repo.save(chain_dir, state)
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
            self._state_repo.save(chain_dir, state)
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
        from ..domain.step_result import StepResult

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
        self._state_repo.save(chain_dir, state)
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
                self._state_repo.save(chain_dir, advanced)
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
        workflow_repo: WorkflowRepository,
        state_repo: WorkflowStateRepository,
        script_runner: ScriptRunner,
        event_dispatcher: Any = None,
        event_log: Any = None,
    ):
        self._workflow_repo = workflow_repo
        self._state_repo = state_repo
        self._script_runner = script_runner
        self._event_dispatcher = event_dispatcher
        self._event_log = event_log
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
        workflow = self._workflow_repo.load(chain_dir)
        state = self._state_repo.load(chain_dir)

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
            from ..domain.contracts import ContractEvaluator

            pre_errors = ContractEvaluator.evaluate_pre(state, gate.contract)
            if pre_errors:
                raise TransitionBlockedError(
                    f"Gate '{gate_name}' contract.pre bloqueado", pre_errors
                )

        # ── 4. Execute gate validation script ───────────────────
        if gate.gate_validation_script:
            vresult = self._script_runner.run(
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
            from .context_resolver import ContextResolver

            state = ContextResolver().promote_provides(state, source_node)

        # ── 6b. Aplicar context_updates de la edge ────────────────
        if gate.context_updates:
            from .context_resolver import ContextResolver

            cr = ContextResolver()
            state = cr.apply_context_updates(state, gate.context_updates)

        # ── 6c. Sincronizar SessionRecord con contexto promovido ──
        if state.context.get("project_id"):
            try:
                from ..domain.session import SessionStore

                record = SessionStore.get_active()
                if record:
                    record.project_id = state.context["project_id"]
                    record.chain_dir = chain_dir
                    SessionStore.save(record)
            except Exception:
                pass

        # ── 6d. Evaluate contract.post DESPUES de transicionar ──
        # El post describe una condicion del ESTADO DESTINO (nuevo estado).
        # Solo puede verificarse tras aplicar la transicion, no antes.
        if gate.contract:
            from ..domain.contracts import ContractEvaluator

            post_errors = ContractEvaluator.evaluate_post(state, gate.contract)
            if post_errors:
                raise TransitionBlockedError(
                    f"Gate '{gate_name}' contract.post bloqueado", post_errors
                )

        self._state_repo.save(chain_dir, state)
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


class AdvanceSubStateUseCase:
    """Caso de uso: Avanzar al siguiente sub-estado dentro del mismo nodo.

    No requiere gate. Usa on_success del sub-estado actual.
    Diseñado para user_input después de que el agente presente resultados.
    """

    def __init__(
        self,
        workflow_repo: WorkflowRepository,
        state_repo: WorkflowStateRepository,
    ):
        self._workflow_repo = workflow_repo
        self._state_repo = state_repo

    def execute(self, chain_dir: str | ExecutionContext) -> dict:
        if isinstance(chain_dir, ExecutionContext):
            chain_dir = chain_dir.chain_dir

        workflow = self._workflow_repo.load(chain_dir)
        state = self._state_repo.load(chain_dir)
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

        self._state_repo.save(chain_dir, state)
        return {
            "ok": True,
            "from": sub_state.key,
            "to": state.current_sub_state,
            "state": state,
        }


class ReplayUseCase:
    """Caso de uso: Reconstruir estado desde event log.

    SRP: Unica responsabilidad = replay de eventos.
    """

    def __init__(self, state_repo: WorkflowStateRepository):
        self._state_repo = state_repo

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
            api = WorkflowAPI(chain_dir=chain_dir, project_id=project_id)
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
                "informal": _is_informal_sprint(chain_dir, project_id),
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


def _is_informal_sprint(chain_dir: str, project_id: str) -> bool:
    """Detecta si el sprint del chain_dir es informal (session log).

    Un sprint informal (session-YYYYMMDD) se crea por capture_task con el
    flag informal=true en el nodo spring del delta. Consulta el delta para
    distinguirlo del sprint formal del workflow.
    """
    import os

    if not chain_dir or not project_id:
        return False
    try:
        from libs.path_resolver import get_project_dir
        from features.delta_tracking import SpringService

        project_root = str(get_project_dir(project_id))
        if not os.path.isdir(project_root):
            return False
        spring_id = _sprint_id_of(chain_dir)
        spring_svc = SpringService(project_root)
        spring = spring_svc.get_spring(spring_id)
        return bool(spring and spring.get("informal"))
    except Exception:
        return False


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
            try:
                chain_dir = state.context.get("chain_dir", "")
                if chain_dir:
                    from features.delta_tracking import spring_service

                    spring_svc = spring_service(chain_dir=chain_dir)
                    if spring_svc:
                        spring_id = spring_svc.get_spring_id(chain_dir)
                        spring_svc.ensure_spring_imported(chain_dir, spring_id)
                        specs = spring_svc.get_specs(spring_id)
                        total = len(specs)
                        ctx_state = spring_svc.get_spring_context(spring_id)
                        current = int(ctx_state.get("current_spec_index", 0))
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
