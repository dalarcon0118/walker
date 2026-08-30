"""application/step_orchestrator.py — StepOrchestrator.

SRP: Unica responsabilidad = evaluar el ExecutionType del sub-estado
actual y decidir la accion para el LLM (execute / await_submit / manual).

Decide qué se le presenta al agente: prompt resuelto, briefing del nodo,
opciones de user_input, o auto-transición cuando aplica.

Extraido de OrchestrationService (god class). El OrchestrationService
queda como fachada y delega aqui.
"""

from __future__ import annotations

from typing import Any

from returns.pipeline import is_successful

from ..domain.models import ExecutionType
from ..domain.events import WorkflowEvent
from ..application.context_resolver import ContextResolver


class StepOrchestrator:
    """Decide el siguiente paso del sub-estado actual."""

    def __init__(
        self,
        workflow_store: Any,
        tracer: Any,
        submit_uc: Any,
        handle_error: Any,
        build_node_briefing: Any,
        resolve_prompt: Any,
        transition: Any,
        signal_bus: Any = None,
    ):
        self._workflow_store = workflow_store
        self._tracer = tracer
        self._submit_uc = submit_uc
        self._handle_error = handle_error
        self._build_node_briefing = build_node_briefing
        self._resolve_prompt = resolve_prompt
        self._transition = transition
        self._signal_bus = signal_bus

    def _check_pending_signals(self, state: Any, workflow: Any) -> dict | None:
        """P1: Check if current wait state has received a waited signal.

        Returns:
            dict with action='signal_received' if signal found, None otherwise.
        """
        if not self._signal_bus or not state.blocked_at:
            return None

        node = workflow.get_node(state.current_node)
        if not node:
            return None

        sub_state = node.find_sub_state(state.current_sub_state)
        if not sub_state or not sub_state.waits_for:
            return None

        pending = getattr(state, "pending_signals", [])
        if not pending:
            return None

        for signal in pending:
            if signal in sub_state.waits_for:
                strategy = sub_state.on_signal or "auto_advance"
                result = BlockResolver.resolve(
                    state,
                    strategy,
                    {
                        "sub_state": state.current_sub_state,
                        "reason": f"signal '{signal}' received",
                    },
                )
                if is_successful(result):
                    new_state = result.value_or(state)
                    self._workflow_store.save_state(new_state, state.context.get("chain_dir", ""))
                    self._tracer.trace_event(
                        WorkflowEvent.of(
                            "signal_resolved",
                            payload={
                                "signal": signal,
                                "strategy": strategy,
                                "sub_state": state.current_sub_state,
                                "node": state.current_node,
                            },
                            task_id=ctx.task_id,
                            session_id=ctx.session_id,
                        )
                    )
                    return {
                        "action": "signal_received",
                        "strategy": strategy,
                        "signal": signal,
                        "message": f"Wait state resumed by signal '{signal}'. Resolved with '{strategy}'.",
                        "sub_state": state.current_sub_state,
                    }
                else:
                    self._tracer.trace_event(
                        WorkflowEvent.of(
                            "signal_resolution_failed",
                            payload={
                                "reason": result.failure(),
                                "signal": signal,
                                "sub_state": state.current_sub_state,
                                "node": state.current_node,
                            },
                            task_id=ctx.task_id,
                            session_id=ctx.session_id,
                        )
                    )
                    return {
                        "action": "error",
                        "message": f"Signal resolution failed: {result.failure()}",
                        "sub_state": state.current_sub_state,
                    }

        return None

    def _check_timeout(self, state: Any, workflow: Any) -> dict | None:
        """P0: Check if current wait state has timed out.

        Returns:
            dict with action='timeout' if timed out, None otherwise.
        """
        if not state.blocked_at or state.block_timeout_ms <= 0:
            return None

        from datetime import datetime, timezone

        try:
            block_since = datetime.fromisoformat(state.block_since)
            now = datetime.now(timezone.utc)
            elapsed_ms = (now - block_since).total_seconds() * 1000
        except Exception:
            return None

        if elapsed_ms >= state.block_timeout_ms:
            # Timeout expired - resolve with on_timeout strategy
            node = workflow.get_node(state.current_node)
            if not node:
                return None

            sub_state = node.find_sub_state(state.current_sub_state)
            if not sub_state:
                return None

            strategy = sub_state.on_timeout or "skip"

            result = BlockResolver.resolve(
                state,
                strategy,
                {
                    "sub_state": state.current_sub_state,
                    "reason": f"timeout after {elapsed_ms:.0f}ms",
                },
            )
            if is_successful(result):
                new_state = result.value_or(state)
                self._workflow_store.save_state(new_state, state.context.get("chain_dir", ""))
                self._tracer.trace_event(
                    WorkflowEvent.of(
                        "timeout_resolved",
                        payload={
                            "strategy": strategy,
                            "elapsed_ms": elapsed_ms,
                            "timeout_ms": state.block_timeout_ms,
                            "sub_state": state.current_sub_state,
                            "node": state.current_node,
                        },
                        task_id=ctx.task_id,
                        session_id=ctx.session_id,
                    )
                )
                return {
                    "action": "timeout",
                    "strategy": strategy,
                    "message": f"Wait state timed out after {elapsed_ms:.0f}ms. Resolved with '{strategy}'.",
                    "sub_state": state.current_sub_state,
                }
            else:
                self._tracer.trace_event(
                    WorkflowEvent.of(
                        "timeout_resolution_failed",
                        payload={
                            "reason": result.failure(),
                            "sub_state": state.current_sub_state,
                            "node": state.current_node,
                        },
                        task_id=ctx.task_id,
                        session_id=ctx.session_id,
                    )
                )
                return {
                    "action": "error",
                    "message": f"Timeout resolution failed: {result.failure()}",
                    "sub_state": state.current_sub_state,
                }

        return None

    def step(self, ctx: Any) -> dict:
        """Evalúa el ExecutionType del sub-estado actual y decide la acción."""
        from ..domain.ports_registry import resolve_port
        from ..domain.ports import ISessionStore

        workflow = self._workflow_store.load_graph()
        state = self._workflow_store.load_state(ctx.chain_dir)

        # P0: Check timeout for wait states
        timeout_result = self._check_timeout(state, workflow)
        if timeout_result:
            return timeout_result

        # P1: Check pending signals for wait states
        signal_result = self._check_pending_signals(state, workflow)
        if signal_result:
            return signal_result

        node = workflow.get_node(state.current_node)
        if not node:
            return {
                "action": "error",
                "message": f"Nodo '{state.current_node}' no existe",
                "execution_type": "unknown",
                "sub_state": state.current_sub_state,
            }

        sub_state = node.find_sub_state(state.current_sub_state)
        if not sub_state:
            return {
                "action": "error",
                "message": f"Sub-estado '{state.current_sub_state}' no existe en '{state.current_node}'",
                "execution_type": "unknown",
                "sub_state": state.current_sub_state,
            }

        # ── Resolver contexto via ContextResolver ──────────────────
        try:
            _store = resolve_port(ISessionStore)
            session = _store.load(ctx.session_id) if ctx.session_id and _store else None
            resolver = ContextResolver()
            resolved_ctx = resolver.resolve_for_sub_state(
                state, node, sub_state, session
            )
            ctx.baggage.update(resolved_ctx)
        except Exception as e:
            return {
                "action": "error",
                "message": f"[context] {e}",
                "execution_type": "unknown",
                "sub_state": state.current_sub_state,
            }

        execution = sub_state.execution
        node_briefing = self._build_node_briefing(node, sub_state)

        # ── Trazar entrada a sub-estado ─────────────────────────────
        self._tracer.trace_event(
            WorkflowEvent.of(
                "NODE_ENTRY",
                payload={
                    "node": state.current_node,
                    "sub_state": state.current_sub_state,
                    "execution_type": execution.value,
                    "sub_state_index": state.sub_state_index,
                },
                task_id=ctx.task_id,
                session_id=ctx.session_id,
            )
        )

        # ── AUTO / BOOTSTRAP: ejecutar pipeline completo ────────────
        if execution in (ExecutionType.AUTO, ExecutionType.BOOTSTRAP):
            return self._step_auto(ctx, state, sub_state, execution)

        # ── LLM_TASK: resolver prompt_id o mostrar on_entry_message ─
        if execution == ExecutionType.LLM_TASK:
            return self._step_llm(ctx, state, sub_state, execution, node_briefing)

        # ── USER_INPUT: resolver prompt_id o mostrar on_entry_message ─
        if execution == ExecutionType.USER_INPUT:
            return self._step_user_input(ctx, state, sub_state, node_briefing)

        # ── MANUAL: resolver prompt_id o mostrar on_entry_message ──
        return self._step_manual(ctx, state, sub_state, execution, node_briefing)

    def _step_auto(self, ctx: Any, state: Any, sub_state: Any, execution: Any) -> dict:
        """Ejecuta pipeline completo para sub-estados auto/bootstrap."""
        from ..domain.errors import WorkflowEngineError

        try:
            result = self._submit_uc.execute(ctx)
            task_data = result.get("task_data", {})
            self._tracer.trace_auto(
                ctx.chain_dir,
                state.current_sub_state,
                stdout=str(task_data.get("stdout", "")),
                stderr=str(task_data.get("stderr", "")),
                exit_code=result.get("exit_code", 0),
                task_id=ctx.task_id,
            )
            return {
                "action": "execute",
                "execution_type": execution.value,
                "sub_state": state.current_sub_state,
                "result": result,
                "message": result.get("advanced_to", "auto-completed"),
            }
        except WorkflowEngineError as e:
            error_result = self._handle_error(ctx, e, sub_state)
            error_result.update(
                {
                    "execution_type": execution.value,
                    "sub_state": state.current_sub_state,
                }
            )
            self._tracer.trace_error(
                ctx.chain_dir,
                type(e).__name__,
                str(e),
                task_id=ctx.task_id,
            )
            return error_result

    def _resolve_and_prepend(
        self, ctx: Any, sub_state: Any, node_briefing: str
    ) -> tuple[str, bool]:
        """Resuelve el prompt del sub-estado (con briefing prepended)."""
        if not sub_state.prompt_id:
            msg = sub_state.prompt or sub_state.on_entry_message or ""
            if node_briefing:
                msg = f"{node_briefing}\n\n---\n\n{msg}"
            return msg, False

        sprint_ctx = {}  # contexto de placeholders: incumbencia del host
        context_config = getattr(sub_state, "context", None)
        resolved = self._resolve_prompt(
            sub_state.prompt_id,
            ctx.chain_dir,
            task_id=ctx.task_id,
            context_config=context_config,
            execution_ctx=ctx,
            extra_data=sprint_ctx,
        )
        if node_briefing:
            resolved = f"{node_briefing}\n\n---\n\n{resolved}"
        return resolved, True

    def _step_llm(
        self, ctx: Any, state: Any, sub_state: Any, execution: Any, node_briefing: str
    ) -> dict:
        """Resuelve el prompt para un sub-estado LLM_TASK."""
        message, resolved = self._resolve_and_prepend(ctx, sub_state, node_briefing)
        return {
            "action": "await_submit",
            "execution_type": execution.value,
            "sub_state": state.current_sub_state,
            "message": message,
            "resolved_prompt": resolved,
            "required_fields": sub_state.required_fields,
        }

    def _step_user_input(
        self, ctx: Any, state: Any, sub_state: Any, node_briefing: str
    ) -> dict:
        """Resuelve el prompt para un sub-estado USER_INPUT."""
        options = [
            {"value": o.value, "label": o.label, "description": o.description}
            for o in (sub_state.options or [])
        ]
        message, resolved = self._resolve_and_prepend(ctx, sub_state, node_briefing)
        return {
            "action": "await_submit",
            "execution_type": ExecutionType.USER_INPUT.value,
            "sub_state": state.current_sub_state,
            "message": message,
            "resolved_prompt": resolved,
            "options": options,
            "required_fields": sub_state.required_fields,
        }

    def _step_manual(
        self, ctx: Any, state: Any, sub_state: Any, execution: Any, node_briefing: str
    ) -> dict:
        """Maneja sub-estados MANUAL (con auto-transicion si aplica)."""
        # Verificar auto-transition: si el sub-estado no necesita más
        # datos y hay exactamente una gate disponible, transicionar.
        needs_input = bool(sub_state.required_fields)
        state_after = self._workflow_store.load_state(ctx.chain_dir)
        wf_after = self._workflow_store.load_graph()
        gates = wf_after.edges_by_source.get(state_after.current_node, [])
        can_auto = not needs_input and len(gates) == 1

        if can_auto:
            gate_name = gates[0].name
            self._tracer.trace_event(
                WorkflowEvent.of(
                    "AUTO_TRANSITION",
                    payload={"gate": gate_name},
                    task_id=ctx.task_id,
                )
            )
            return self._transition(ctx, gate_name)

        message, resolved = self._resolve_and_prepend(ctx, sub_state, node_briefing)
        return {
            "action": "manual",
            "execution_type": execution.value,
            "sub_state": state.current_sub_state,
            "message": message,
            "resolved_prompt": resolved,
            "required_fields": sub_state.required_fields,
        }
