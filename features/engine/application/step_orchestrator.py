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

from ..domain.models import ExecutionType, WorkflowGraph, WorkflowState
from ..application.context_resolver import ContextResolver


class StepOrchestrator:
    """Decide el siguiente paso del sub-estado actual."""

    def __init__(
        self,
        workflow_repo: Any,
        state_repo: Any,
        tracer: Any,
        submit_uc: Any,
        handle_error: Any,
        build_node_briefing: Any,
        build_prompt_context: Any,
        resolve_prompt: Any,
        transition: Any,
    ):
        self._workflow_repo = workflow_repo
        self._state_repo = state_repo
        self._tracer = tracer
        self._submit_uc = submit_uc
        self._handle_error = handle_error
        self._build_node_briefing = build_node_briefing
        self._build_prompt_context = build_prompt_context
        self._resolve_prompt = resolve_prompt
        self._transition = transition

    def step(self, ctx: Any) -> dict:
        """Evalúa el ExecutionType del sub-estado actual y decide la acción."""
        from ..domain.session import SessionStore
        from ..domain.errors import WorkflowEngineError

        workflow = self._workflow_repo.load(ctx.chain_dir)
        state = self._state_repo.load(ctx.chain_dir)
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
            session = SessionStore.load(ctx.session_id) if ctx.session_id else None
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
        self._tracer.trace(
            ctx.chain_dir,
            "NODE_ENTRY",
            data={
                "node": state.current_node,
                "sub_state": state.current_sub_state,
                "execution_type": execution.value,
                "sub_state_index": state.sub_state_index,
            },
            task_id=ctx.task_id,
            session_id=ctx.session_id,
        )

        # ── AUTO / BOOTSTRAP: ejecutar pipeline completo ────────────
        if execution in (ExecutionType.AUTO, ExecutionType.BOOTSTRAP):
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

        # ── LLM_TASK: resolver prompt_id o mostrar on_entry_message ─
        if execution == ExecutionType.LLM_TASK:
            if sub_state.prompt_id:
                sprint_ctx = self._build_prompt_context(ctx.chain_dir, ctx.task_id)
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
                return {
                    "action": "await_submit",
                    "execution_type": execution.value,
                    "sub_state": state.current_sub_state,
                    "message": resolved,
                    "resolved_prompt": True,
                    "required_fields": sub_state.required_fields,
                }
            prompt = sub_state.prompt or sub_state.on_entry_message or ""
            if node_briefing:
                prompt = f"{node_briefing}\n\n---\n\n{prompt}"
            return {
                "action": "await_submit",
                "execution_type": execution.value,
                "sub_state": state.current_sub_state,
                "message": prompt,
                "resolved_prompt": False,
                "required_fields": sub_state.required_fields,
            }

        # ── USER_INPUT: resolver prompt_id o mostrar on_entry_message ─
        if execution == ExecutionType.USER_INPUT:
            options = [
                {"value": o.value, "label": o.label, "description": o.description}
                for o in (sub_state.options or [])
            ]
            if sub_state.prompt_id:
                sprint_ctx = self._build_prompt_context(ctx.chain_dir, ctx.task_id)
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
                return {
                    "action": "await_submit",
                    "execution_type": execution.value,
                    "sub_state": state.current_sub_state,
                    "message": resolved,
                    "resolved_prompt": True,
                    "options": options,
                    "required_fields": sub_state.required_fields,
                }
            msg = sub_state.on_entry_message or ""
            if node_briefing:
                msg = f"{node_briefing}\n\n---\n\n{msg}"
            return {
                "action": "await_submit",
                "execution_type": execution.value,
                "sub_state": state.current_sub_state,
                "message": msg,
                "resolved_prompt": False,
                "options": options,
                "required_fields": sub_state.required_fields,
            }

        # ── MANUAL: resolver prompt_id o mostrar on_entry_message ──
        else:
            # Verificar auto-transition: si el sub-estado no necesita más
            # datos y hay exactamente una gate disponible, transicionar.
            needs_input = bool(sub_state.required_fields)
            state_after = self._state_repo.load(ctx.chain_dir)
            wf_after = self._workflow_repo.load(ctx.chain_dir)
            gates = wf_after.edges_by_source.get(state_after.current_node, [])
            can_auto = not needs_input and len(gates) == 1

            if can_auto:
                gate_name = gates[0].name
                self._tracer.trace(
                    ctx.chain_dir,
                    "AUTO_TRANSITION",
                    data={"gate": gate_name},
                    task_id=ctx.task_id,
                )
                return self._transition(ctx, gate_name)

            if sub_state.prompt_id:
                sprint_ctx = self._build_prompt_context(ctx.chain_dir, ctx.task_id)
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
                return {
                    "action": "manual",
                    "execution_type": execution.value,
                    "sub_state": state.current_sub_state,
                    "message": resolved,
                    "resolved_prompt": True,
                    "required_fields": sub_state.required_fields,
                }
            msg = sub_state.on_entry_message or ""
            if node_briefing:
                msg = f"{node_briefing}\n\n---\n\n{msg}"
            return {
                "action": "manual",
                "execution_type": execution.value,
                "sub_state": state.current_sub_state,
                "message": msg,
                "resolved_prompt": False,
                "required_fields": sub_state.required_fields,
            }
