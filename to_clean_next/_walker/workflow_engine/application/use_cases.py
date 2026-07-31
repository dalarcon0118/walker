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

from typing import Any

from ..domain.models import (
    WorkflowGraph,
    SprintState,
    DomainEvent,
    DomainEventType,
    Gate,
    ExecutionContext,
    ExecutionType,
)
from ..domain.registry import TaskRegistry
from ..domain.services import ValidationService, AdvanceService, TransitionService
from ..domain.errors import (
    TransitionBlockedError,
    GateValidationError,
    ValidationError,
    SubStateNotFoundError,
    AutoAdvanceError,
)
from ..infrastructure.repositories import (
    WorkflowRepository,
    SprintStateRepository,
    ScriptRunner,
)
from ..infrastructure.side_effects import SideEffectEngine
from ...contracts import ContractValidator, Contract


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
        state_repo: SprintStateRepository,
    ):
        self._workflow_repo = workflow_repo
        self._state_repo = state_repo

    def execute(self, chain_dir: str) -> dict:
        workflow = self._workflow_repo.load(chain_dir)
        state = self._state_repo.load(chain_dir)
        return self._validate(workflow, state)

    def _validate(self, workflow: WorkflowGraph, state: SprintState) -> dict:
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

        if sub_state and sub_state.required_fields:
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
        state_repo: SprintStateRepository,
        script_runner: ScriptRunner,
        task_registry: TaskRegistry | None = None,
        contract_validator: type[ContractValidator] | None = None,
        side_effect_engine: SideEffectEngine | None = None,
        event_dispatcher: Any = None,
        event_log: Any = None,
    ):
        self._workflow_repo = workflow_repo
        self._state_repo = state_repo
        self._script_runner = script_runner
        self._task_registry = task_registry
        self._contract_validator = contract_validator or ContractValidator
        self._side_effect_engine = side_effect_engine or SideEffectEngine()
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
        # ── 0. Normalizar parametros: soportar ExecutionContext ──
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

        # ── 2. Validate required fields ────────────────────────
        field_errors = ValidationService.validate_sub_state_data(sub_state, fields)
        if field_errors:
            raise ValidationError("Campos requeridos faltantes", field_errors)

        # ── 3. ContractValidator.validate_input() ───────────────
        if sub_state.input_schema and self._contract_validator:
            contract = Contract.from_dict(sub_state.input_schema)
            report = self._contract_validator.verify_pre(contract, fields)
            if not report.passed:
                errors = [v.message for v in report.errors]
                raise ValidationError("Contract validation (input) failed", errors)

        # ── 4. Persist submitted data ──────────────────────────
        state.record_submit(state.current_sub_state, fields)
        if task_id:
            state.context["task_id"] = task_id
        state.context["chain_dir"] = chain_dir
        self._state_repo.save(chain_dir, state)
        self.events.append(
            DomainEvent(
                DomainEventType.DATA_SUBMITTED,
                data={"sub_state": state.current_sub_state, "fields": fields},
            )
        )

        result = {
            "submitted": True,
            "sub_state": state.current_sub_state,
            "field_count": len(fields),
        }

        # ── 5. TaskRegistry.execute() si sub-estado tiene action ─
        task_result = None
        if sub_state.action and self._task_registry:
            # Crear ExecutionContext si no tenemos uno
            exec_ctx = ctx or ExecutionContext.create(
                session_id=chain_dir.split("/")[-1],
                chain_dir=chain_dir,
                task_id=task_id or "",
            )
            task_result = self._task_registry.execute(
                sub_state.action, exec_ctx, fields
            )
            # ── 5a. Recargar state DESPUES de ejecutar la tarea ──
            # Las tareas (spec_task, save_task_spec, etc.) pueden
            # modificar sprint-state.json directamente. Sin esta recarga,
            # el save() posterior sobreescribiria esos cambios con el
            # state en memoria (obsoleto).
            state = self._state_repo.load(chain_dir)
            # Persistir resultado de la tarea como evidencia
            state.record_submit(
                state.current_sub_state,
                {
                    "task_action": sub_state.action,
                    "task_ok": task_result.ok,
                    "task_data": str(task_result.data)[:1000]
                    if task_result.data
                    else "",
                },
            )
            self._state_repo.save(chain_dir, state)
            if not task_result.ok:
                error_msg = (
                    str(task_result.error) if task_result.error else "Unknown error"
                )
                raise AutoAdvanceError(state.current_node, sub_state.key, 1, error_msg)
            result["task_executed"] = True
            result["task_action"] = sub_state.action
            result["task_result"] = (
                str(task_result.data)[:500] if task_result.data else ""
            )

        # ── 6. ContractValidator.validate_output() ──────────────
        if sub_state.output_schema and self._contract_validator and task_result:
            output_data = task_result.data if isinstance(task_result.data, dict) else {}
            contract = Contract.from_dict(sub_state.output_schema)
            report = self._contract_validator.verify_post(contract, output_data)
            if not report.passed:
                warnings = [v.message for v in report.warnings]
                if warnings:
                    result["output_warnings"] = warnings

        # ── 7. SideEffectEngine.execute() ──────────────────────
        if sub_state.side_effects and self._side_effect_engine:
            exec_ctx = ctx or ExecutionContext.create(
                session_id=chain_dir.split("/")[-1],
                chain_dir=chain_dir,
                task_id=task_id or "",
            )
            side_results = self._side_effect_engine.execute(
                exec_ctx, sub_state.side_effects
            )
            # ── 7a. Recargar state DESPUES de side-effects ──────
            # Las side-effects (advance_task, etc.) modifican
            # sprint-state.json directamente. Recargar evita que
            # el save() las sobreescriba.
            state = self._state_repo.load(chain_dir)
            state.record_submit(
                state.current_sub_state,
                {
                    "side_effects": side_results,
                },
            )
            self._state_repo.save(chain_dir, state)
            result["side_effects"] = side_results

        # ── 8. Auto-execute legacy script si mode=auto ──────────
        # (para backward compat con workflows que usan auto_complete)
        auto_result = None
        if sub_state.is_auto() and sub_state.auto_complete_script:
            auto_result = self._script_runner.run(
                sub_state.auto_complete_script, chain_dir, task_id
            )
            state.record_submit(
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
                    AdvanceService.loop_back(state, workflow, sub_state.key)
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

        # ── 9. Auto-advance ─────────────────────────────────────
        # Avanza si on_success está definido Y:
        #   - execution=auto/bootstrap (siempre)
        #   - execution=llm_task con action Y task ejecutada OK
        #   - execution=llm_task sin action (solo output_schema)
        # NOTA: user_input NO auto-avanza. Requiere transition explícita
        # para forzar que el agente presente el prompt al usuario antes
        # de decidir. Sin esto, el agente puede saltarse el prompt y
        # auto-aprobar sin mostrar resultados.
        task_success = task_result is not None and task_result.ok
        should_advance = sub_state.on_success and (
            sub_state.is_auto()
            or (sub_state.execution == ExecutionType.LLM_TASK and (task_success or not sub_state.action))
        )
        if should_advance:
            advanced = AdvanceService.advance(state, workflow)
            if advanced:
                self._state_repo.save(chain_dir, state)
                self.events.append(
                    DomainEvent(
                        DomainEventType.STATE_TRANSITIONED,
                        data={"from": sub_state.key, "to": state.current_sub_state},
                    )
                )
                result["advanced_to"] = state.current_sub_state
                result["node_complete"] = ValidationService.is_node_complete(
                    state, workflow
                )
            else:
                result["node_complete"] = True
        else:
            # Sub-estados user_input o llm_task sin on_success no auto-avanzan
            result["node_complete"] = ValidationService.is_node_complete(
                state, workflow
            )

        # ── 10. Dispatch events via EventDispatcher ─────────────
        _flush_events(
            self.events,
            self._event_log,
            self._event_dispatcher,
            chain_dir,
            task_id,
        )

        return result

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
        state_repo: SprintStateRepository,
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
        gate = self._find_gate(workflow, state.current_node, gate_name)
        if not gate:
            raise ValidationError(
                f"Gate '{gate_name}' no disponible desde '{state.current_node}'"
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

        # ── 6. Apply transition ─────────────────────────────────
        TransitionService.apply(
            state, workflow, gate.target_node, gate.target_sub_state
        )
        if reason:
            state.context["last_reason"] = reason
        if task_id:
            state.context["task_id"] = task_id
        state.context["chain_dir"] = chain_dir

        # ── 6a. Promover provides del nodo origen al global state ──
        if source_node and source_node.provides:
            from .context_resolver import ContextResolver

            cr = ContextResolver()
            promoted = cr.promote_provides(state, source_node)
            # Si se promovieron datos, actualizar context en el nuevo estado
            for k in promoted:
                if k in source_node.provides:
                    pass  # ya fue escrito por promote_provides

        # ── 6b. Aplicar context_updates de la edge ────────────────
        if gate.context_updates:
            from .context_resolver import ContextResolver

            cr = ContextResolver()
            cr.apply_context_updates(state, gate.context_updates)

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

    def _find_gate(
        self, graph: WorkflowGraph, node_id: str, gate_name: str
    ) -> Gate | None:
        """Busca gate en el nodo o en gates globales."""
        return graph.find_global_gate(node_id, gate_name)

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
        state_repo: SprintStateRepository,
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
            return {"ok": False, "error": f"Sub-estado '{state.current_sub_state}' no encontrado"}

        if not sub_state.on_success:
            return {"ok": False, "error": "Sub-estado actual no tiene on_success definido"}

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

    def __init__(self, state_repo: SprintStateRepository):
        self._state_repo = state_repo

    def execute(self, chain_dir: str, event_log: list[dict]) -> SprintState:
        """Reconstruye estado aplicando eventos secuencialmente."""
        state = SprintState()
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
