"""
application/orchestrator.py — OrchestrationService: entry point unificado.

Integra:
  - SubmitDataUseCase (pipeline: validate → persist → TaskRegistry → SideEffectEngine → auto-advance)
  - TransitionUseCase (inter-node con pre/post contracts)
  - ValidateCurrentStateUseCase (diagnóstico)
  - WorkflowRepository + WorkflowStateRepository

SRP: Unifica step/submit/transition/status como entry point único del engine.
SSOT: Toda operación del workflow pasa por este servicio.
"""

from __future__ import annotations

from typing import Any

from shared.core.engine.domain.ports import (
    IWorkflowIO,
    IWorkflowStore,
)

from ..domain.workflow_action import ActionCatalog
from ..domain.models import (
    ExecutionContext,
    SubState,
    WorkflowNode,
)
from ..domain.errors import (
    ErrorType,
    ErrorStrategy,
)
from ..infrastructure.workflow_store import WorkflowStoreAdapter
from ..infrastructure.trace_log import TraceLogger
from ..domain.ports import ITracer
from ..domain.services.signal_bus import SignalBus
from ..context import (
    ContextComposer,
    ContextProviderRegistry,
)
from shared.core.engine.domain.contract_validator import ContractValidator
from .use_cases import (
    SubmitDataUseCase,
    TransitionUseCase,
    ValidateCurrentStateUseCase,
)
from .prompt_service import PromptService
from .event_dispatch_service import EventDispatchService


class OrchestrationService:
    """Entry point unificado del workflow engine.

    Métodos públicos:
        step(ctx):          Evalúa ExecutionType y decide acción
        submit(ctx, fields): Ejecuta pipeline completo de submit
        transition(ctx, gate): Transiciona entre nodos con contracts
        status(ctx):        Snapshot del estado actual

    Composition root OBLIGATORIO (capa_rout.md D1): el HOST instancia el motor
    pasándole TODAS sus dependencias (patrón servidor / constructor injection).
    El motor no importa el contenedor DI ni construye concreto alguno; recibe un
    ``EngineBundle`` (VO) con todo lo que necesita. El host construye el VO y se
    lo inyecta a ``workflow_api``:
        from shared import Engine, EngineBundle          # shared exporta motor + VO
        from shared.libs.module_system.registry import module_registry
        bundle = EngineBundle(
            workflow_store=WorkflowStoreAdapter(
                workflow_repo=module_registry.get_service("workflow_repo"),
                state_repo=module_registry.get_service("state_repo"),
            ),
            actions=ActionCatalog(
                task_registry=module_registry.get_service("task_registry"),
                side_effect_engine=module_registry.get_service("side_effect_engine"),
            ),
            workflow_io=module_registry.resolve_port(IWorkflowIO),
        )
        engine = Engine(**bundle.to_orchestrator_kwargs())   # o pasar kwargs explícitos
        result = engine.step(exec_ctx)     # auto → ejecuta, manual → espera submit
        result = engine.submit(exec_ctx, {"decision": "approve"})
        result = engine.transition(exec_ctx, "task_complete")
        snapshot = engine.status(exec_ctx)
    """

    def __init__(
        self,
        workflow_store: IWorkflowStore,
        actions: ActionCatalog,
        workflow_io: IWorkflowIO,
        contract_validator: type[ContractValidator] | None = None,
        event_dispatcher: Any = None,
        event_log: Any = None,
        tracer: ITracer | None = None,
        signal_bus: SignalBus | None = None,
        context_registry: ContextProviderRegistry | None = None,
        outbox: Any = None,
    ):
        # Patrón servidor (constructor injection): el motor NO sabe quién lo usa ni
        # de dónde vienen sus dependencias. PUERTOS UNIFICADOS (ADR-WALKER-067
        # §7.2): `workflow_store` (grafo+estado) y `actions` (ActionCatalog:
        # tasks + side-effects). El engine NO recibe repos/registries concretos;
        # los use cases / sub-orquestadores consumen los puertos.
        self._workflow_store = workflow_store
        self._actions = actions
        self._workflow_io = workflow_io
        from ..infrastructure.trace_log import NoopTracer

        self._tracer = tracer or NoopTracer()
        from ..domain.ports_registry import resolve_port
        from ..domain.ports import IEngineMetrics

        self._metrics = resolve_port(IEngineMetrics)

        # Composition root obligatorio (capa_rout.md D1): `actions` es
        # REQUERIDO — no hay defaults que fabriquen un catálogo vacío. Si falta,
        # falla el constructor (fail-fast) — no hay composición parcial.
        if actions is None:
            raise ValueError(
                "OrchestrationService requiere actions (ActionCatalog, composition "
                "root obligatorio). El host debe inyectarlo vía EngineBundle.actions."
            )
        self._contract_validator = contract_validator or ContractValidator
        self._event_dispatcher = event_dispatcher
        self._event_log = event_log
        self._signal_bus = signal_bus or SignalBus()
        self._outbox = outbox

        # Context Composer (Fase 1: opt-in, backwards compat)
        # Registry armado EXTERNAMENTE por el composition root (features/main):
        # el engine puro no sabe qué providers de negocio transporta.
        # context_registry (inyectado) > build_context_registry() > default.
        if context_registry is None:
            try:
                from ..bootstrap import build_context_registry

                _def_reg = build_context_registry()
            except Exception:
                from ..context import create_default_registry

                _def_reg = create_default_registry()
        else:
            _def_reg = context_registry
        self._context_registry: ContextProviderRegistry | None = _def_reg
        self._context_composer: ContextComposer | None = (
            ContextComposer(_def_reg) if _def_reg else None
        )

        # Prompt Manager: centraliza todo lo relacionado a prompts
        self._prompt_manager: Any | None = None

        # VO con TODAS las dependencias del motor (patrón servidor). El host lo
        # inyectó vía los parámetros del constructor; el motor lo conserva para
        # construir WorkflowAPI sin acoplarse a locator/registry externos.
        from .bundle import EngineBundle

        self._bundle = EngineBundle(
            workflow_store=workflow_store,
            actions=actions,
            workflow_io=workflow_io,
            context_registry=context_registry,
            contract_validator=contract_validator,
            event_dispatcher=event_dispatcher,
            event_log=event_log,
            tracer=tracer,
        )

        # Use cases internos (lazy init)
        self._submit_uc: SubmitDataUseCase | None = None
        self._transition_uc: TransitionUseCase | None = None
        self._validate_uc: ValidateCurrentStateUseCase | None = None

        # Servicios SRP (ADR-WALKER-067 F3/C): el facade delega en ellos.
        self._prompt_service = PromptService()
        self._event_dispatch = EventDispatchService(
            workflow_store=workflow_store,
            actions=actions,
            transition_uc_provider=lambda: self.transition_uc,
            event_dispatcher=event_dispatcher,
            event_log=event_log,
            tracer=self._tracer,
        )

    # ── Prompt collection ─────────────────────────────────────────────
    # Los prompts y su contexto son INCUMBENCIA DEL HOST (ADR-WALKER-067): el
    # motor NO conoce IPromptCollector. El host recolecta/resuelve prompts
    # externamente y le pasa al engine lo que necesita para ejecutar.

    @property
    def submit_uc(self) -> SubmitDataUseCase:
        if self._submit_uc is None:
            self._submit_uc = SubmitDataUseCase(
                workflow_store=self._workflow_store,
                actions=self._actions,
                workflow_io=self._workflow_io,
                contract_validator=self._contract_validator,
                event_dispatcher=self._event_dispatcher,
                event_log=self._event_log,
                tracer=self._tracer,
                bundle=self._bundle,
                outbox=self._outbox,
            )
        return self._submit_uc

    @property
    def transition_uc(self) -> TransitionUseCase:
        if self._transition_uc is None:
            self._transition_uc = TransitionUseCase(
                workflow_store=self._workflow_store,
                workflow_io=self._workflow_io,
                event_dispatcher=self._event_dispatcher,
                event_log=self._event_log,
                bundle=self._bundle,
            )
        return self._transition_uc

    @property
    def validate_uc(self) -> ValidateCurrentStateUseCase:
        if self._validate_uc is None:
            self._validate_uc = ValidateCurrentStateUseCase(
                workflow_store=self._workflow_store,
            )
        return self._validate_uc

    # ── Context Composer (lazy) ─────────────────────────────────────

    @property
    def context_composer(self) -> ContextComposer | None:
        """ContextComposer (Fase 1: opt-in). Retorna None si no hay registry."""
        return self._context_composer

    # ── Prompt ID Resolution ────────────────────────────────────────────

    def resolve_prompt(
        self,
        prompt_id: str,
        chain_dir: str,
        task_id: str = "",
        context_config: dict | None = None,
        execution_ctx: Any | None = None,
        extra_data: dict | None = None,
    ) -> str:
        """Resuelve prompt_id (SRP: delega en PromptService)."""
        return self._prompt_service.resolve_prompt(
            prompt_id,
            chain_dir,
            task_id=task_id,
            context_config=context_config,
            execution_ctx=execution_ctx,
            extra_data=extra_data,
        )

    def _resolve_status_prompt(self, sub_state, ctx) -> str:
        """Resuelve el prompt de status (SRP: delega en PromptService)."""
        return self._prompt_service.resolve_status_prompt(sub_state, ctx)

    # ── Node Briefing ─────────────────────────────────────────────

    @staticmethod
    @staticmethod
    def _build_node_briefing(
        node: WorkflowNode | None, sub_state: SubState | None
    ) -> str:
        """Construye el briefing del nodo (SRP: delega en PromptService)."""
        return PromptService.build_node_briefing(node, sub_state)

    # ── Core API ────────────────────────────────────────────────────────

    def step(
        self,
        ctx: ExecutionContext,
    ) -> dict:
        """Evalúa el ExecutionType del sub-estado actual y decide la acción.

        SRP: delega en StepOrchestrator (componente de decisión de paso).
        """
        blocked = self._ensure_not_blocked(ctx, strategy="wait")
        if blocked.get("blocked") and not blocked.get("resolved"):
            return {
                "ok": False,
                "action": "blocked",
                "message": blocked.get("message", "Workflow is blocked"),
                "blocked_at": blocked.get("state", {}).get("blocked_at", ""),
            }

        from .step_orchestrator import StepOrchestrator

        orch = StepOrchestrator(
            workflow_store=self._workflow_store,
            tracer=self._tracer,
            submit_uc=self.submit_uc,
            handle_error=self._handle_error,
            build_node_briefing=self._build_node_briefing,
            resolve_prompt=self.resolve_prompt,
            transition=self.transition,
            signal_bus=self._signal_bus,
        )
        return orch.step(ctx)

    def submit(
        self,
        ctx: ExecutionContext,
        fields: dict[str, Any] | None = None,
    ) -> dict:
        """Ejecuta pipeline completo de submit.

        SRP: delega en SubmitOrchestrator (componente de submit).
        """
        blocked = self._ensure_not_blocked(ctx, strategy="wait")
        if blocked.get("blocked") and not blocked.get("resolved"):
            return {
                "ok": False,
                "action": "blocked",
                "message": blocked.get("message", "Workflow is blocked"),
                "blocked_at": blocked.get("state", {}).get("blocked_at", ""),
            }

        from .submit_orchestrator import SubmitOrchestrator

        orch = SubmitOrchestrator(
            workflow_store=self._workflow_store,
            submit_uc=self.submit_uc,
            handle_error=self._handle_error,
            tracer=self._tracer,
            transition=self.transition,
            dispatch_events_from_side_effects=self._dispatch_events_from_side_effects,
            signal_bus=self._signal_bus,
        )
        import time as _time

        _start = _time.time()
        result = orch.submit(ctx, fields=fields)
        if self._metrics is not None:
            self._metrics.record_submit((_time.time() - _start) * 1000)
        return result

    def advance_sub_state(self, ctx: ExecutionContext) -> dict:
        """Avanza al siguiente sub-estado dentro del mismo nodo.

        No requiere gate. Usa on_success del sub-estado actual para
        determinar el destino. Persiste y retorna el nuevo estado.
        """
        from .use_cases import AdvanceSubStateUseCase

        uc = AdvanceSubStateUseCase(
            workflow_store=self._workflow_store,
        )
        return uc.execute(ctx)

    def transition(
        self,
        ctx: ExecutionContext,
        gate: str,
        reason: str = "",
        skip_preconditions: bool = False,
    ) -> dict:
        """Transiciona entre nodos con validación de pre/post contracts.

        SRP: delega en TransitionOrchestrator (componente de transiciones).
        """
        blocked = self._ensure_not_blocked(ctx, strategy="wait")
        if blocked.get("blocked") and not blocked.get("resolved"):
            return {
                "ok": False,
                "action": "blocked",
                "message": blocked.get("message", "Workflow is blocked"),
                "blocked_at": blocked.get("state", {}).get("blocked_at", ""),
            }

        from .transition_orchestrator import TransitionOrchestrator

        orch = TransitionOrchestrator(
            workflow_store=self._workflow_store,
            transition_uc=self.transition_uc,
            tracer=self._tracer,
            read_edge_transform=self._read_edge_transform,
            execute_edge_transform=self._execute_edge_transform,
            dispatch_event=self._dispatch_event,
        )
        import time as _time

        _start = _time.time()
        result = orch.transition(
            ctx, gate, reason=reason, skip_preconditions=skip_preconditions
        )
        if self._metrics is not None:
            self._metrics.record_transition((_time.time() - _start) * 1000)
        return result

    def _ensure_not_blocked(
        self, ctx: ExecutionContext, strategy: str = "wait", payload: dict | None = None
    ) -> dict:
        """Verifica bloqueo y resuelve si es posible.

        Returns:
            dict con blocked, resolved, state, action, message
        """
        try:
            from returns.result import Success
            from ..domain.services.block_resolver import BlockResolver

            state = self._workflow_store.load_state(ctx.chain_dir)
            workflow = self._workflow_store.load_graph()
            if isinstance(workflow, dict):
                from ..domain.models import WorkflowGraph
                workflow = WorkflowGraph.from_dict(workflow)

            if not BlockResolver.is_blocked(state):
                return {"blocked": False, "resolved": False}

            result = BlockResolver.resolve(
                state, strategy, payload or {}, graph=workflow
            )
            if isinstance(result, Success):
                new_state = result.unwrap()
                self._workflow_store.save_state(new_state, ctx.chain_dir)
                return {
                    "blocked": True,
                    "resolved": True,
                    "state": new_state.to_dict(),
                    "action": strategy,
                    "message": f"Block resolved with strategy '{strategy}'",
                }
            return {"blocked": True, "resolved": False}
        except Exception as e:
            return {"blocked": True, "resolved": False, "message": str(e)}

    def health_metrics(self) -> dict:
        """Snapshot de observabilidad del motor (p99 submit/transition, state).

        ADR-WALKER-067 F5/J: expone las métricas del puerto IEngineMetrics
        (default in-memory; el host puede reemplazarlo con su telemetría).
        """
        if self._metrics is None:
            return {}
        return self._metrics.snapshot()

    def status(
        self,
        ctx: ExecutionContext,
    ) -> dict:
        """Snapshot del estado actual + workflow info.

        SRP: delega en StatusOrchestrator (componente read-only).
        """
        from .status_orchestrator import StatusOrchestrator

        orch = StatusOrchestrator(
            workflow_store=self._workflow_store,
            resolve_status_prompt=self._resolve_status_prompt,
            validate_uc=self.validate_uc,
        )
        return orch.status(ctx)

    # ── Error Handling (TASK-009) ───────────────────────────────────────

    @property
    def _error_handler(self):
        """ErrorHandler: SRP manejo de errores del workflow (lazy)."""
        if getattr(self, "__error_handler", None) is None:
            from .error_handler import ErrorHandler

            self.__error_handler = ErrorHandler(
                submit_uc=self.submit_uc,
                transition_uc=self.transition_uc,
                actions=self._actions,
                workflow_store=self._workflow_store,
                tracer=self._tracer,
            )
        return self.__error_handler

    def _classify_error(self, error: Exception) -> ErrorType:
        """Clasifica un error segun su tipo."""
        return self._error_handler.classify(error)

    def _resolve_error_strategy(
        self,
        error_type: ErrorType,
        sub_state: SubState | None = None,
    ) -> ErrorStrategy:
        """Resuelve la estrategia para un tipo de error."""
        return self._error_handler.resolve_strategy(error_type, sub_state)

    def _handle_error(
        self,
        ctx: ExecutionContext,
        error: Exception,
        sub_state: SubState | None = None,
        fields: dict | None = None,
    ) -> dict:
        """Maneja un error segun la estrategia configurada.

        SRP: delega en ErrorHandler (componente de manejo de errores).
        """
        return self._error_handler.handle(ctx, error, sub_state, fields=fields)

    # ── Edge Transform (TASK-007) ──────────────────────────────────────

    def _read_edge_transform(
        self,
        ctx: ExecutionContext,
        gate_name: str,
    ) -> dict | None:
        """Lee la configuracion de transform del edge ANTES de la transicion.

        Se ejecuta en el nodo fuente (source), antes de que transition
        cambie el estado al nodo destino.

        Returns:
            Dict con transform config o None si no hay transform.
        """
        workflow = self._workflow_store.load_graph()
        state = self._workflow_store.load_state(ctx.chain_dir)
        node = workflow.get_node(state.current_node)
        if not node:
            return None
        for g in workflow.edges_by_source.get(state.current_node, []):
            if g.name == gate_name and g.transform:
                return dict(g.transform)
        return None

    def _execute_edge_transform(
        self,
        ctx: ExecutionContext,
        transform: dict,
    ) -> dict | None:
        """Ejecuta transform del edge.

        Los transforms se definen en edges.json con:
          transform: {
            "action": "nombre_accion",      # TaskRegistry action
            "input": ["NODE.SUB.field"],    # Selectores de sub_state_data
            "output": "NODE.context.field"   # Destino en contexto
          }

        Args:
            ctx: ExecutionContext.
            transform: Configuracion del transform (ya leida del source node).

        Returns:
            Dict con resultado del transform o None si no hay accion.
        """
        action = transform.get("action", "")
        input_selectors = transform.get("input", [])
        output = transform.get("output", "")

        if not action:
            return None

        state = self._workflow_store.load_state(ctx.chain_dir)

        # ── Preparar datos de input desde sub_state_data ──────────
        input_data = {}
        for selector in input_selectors:
            parts = selector.split(".")
            if len(parts) >= 2:
                node_key = parts[0]
                sub_key = parts[1]
                field = parts[2] if len(parts) >= 3 else None

                # Buscar en sub_state_data
                data_key = f"{node_key}.{sub_key}"
                ss_data = state.get_sub_state_data(data_key)
                if field == "*":
                    # Wildcard: todas las fields del sub-estado
                    val = dict(ss_data.fields)
                elif field:
                    val = ss_data.fields.get(field, "")
                else:
                    val = dict(ss_data.fields)
                input_data[selector] = val

        # ── Ejecutar accion via TaskRegistry ──────────────────────
        try:
            exec_ctx = ExecutionContext.create(
                session_id=ctx.session_id,
                chain_dir=ctx.chain_dir,
                task_id=ctx.task_id,
                trace_id=ctx.trace_id,
            )
            task_result = self._actions.execute_action(action, exec_ctx, input_data)

            if not task_result.ok:
                return {
                    "action": action,
                    "ok": False,
                    "error": str(task_result.error)
                    if task_result.error
                    else "Unknown error",
                }

            # ── Persistir resultado en contexto (ELM: state inmutable) ──
            if output and task_result.data:
                output_field = output.split(".")[-1]
                state = state.with_context(output_field, str(task_result.data)[:2000])
                self._workflow_store.save_state(state, ctx.chain_dir)

            return {
                "action": action,
                "ok": True,
                "input_selectors": input_selectors,
                "output": output,
                "result": str(task_result.data)[:500] if task_result.data else None,
            }
        except Exception as e:
            return {
                "action": action,
                "ok": False,
                "error": str(e),
            }

    # ── Event Dispatch (TASK-006: on:{}) ────────────────────────────────

    def _dispatch_events_from_side_effects(
        self,
        ctx: ExecutionContext,
        result: dict,
    ) -> int:
        """Despacha eventos on:{} de side-effects (SRP: delega en EventDispatchService)."""
        return self._event_dispatch.dispatch_events_from_side_effects(ctx, result)

    def _dispatch_event(
        self,
        ctx: ExecutionContext,
        event_name: str,
        result: dict | None = None,
    ) -> dict | None:
        """Ejecuta el handler de un evento on:{} (SRP: delega en EventDispatchService)."""
        return self._event_dispatch.dispatch_event(ctx, event_name, result)

    # ── Helpers ─────────────────────────────────────────────────────────

    def create_context(
        self,
        chain_dir: str,
        task_id: str = "",
        session_id: str = "",
    ) -> ExecutionContext:
        """Crea un ExecutionContext para las operaciones del servicio."""
        import uuid

        return ExecutionContext.create(
            session_id=session_id or f"sess-{uuid.uuid4().hex[:8]}",
            chain_dir=chain_dir,
            task_id=task_id,
        )
