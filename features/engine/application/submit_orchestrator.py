"""application/submit_orchestrator.py — SubmitOrchestrator.

SRP: Unica responsabilidad = ejecutar el pipeline de submit del workflow.

Pipeline:
  1. SubmitDataUseCase.execute() (valida input, ejecuta task, side-effects,
     auto-advance)
  2. Manejo de errores con estrategia (retry/block/compensate/escalate)
  3. Teoria de grafos: XOR-split resuelto por intent (auto-transition)
  4. Registro NodeExecution (saga log) + tracing + eventos on:{}

Extraido de OrchestrationService (god class). El OrchestrationService
queda como fachada y delega aqui.
"""
from __future__ import annotations

import time
from typing import Any

from libs.shared_domain import ExecutionContext


class SubmitOrchestrator:
    """Orquesta el pipeline de submit de datos al sub-estado actual."""

    def __init__(
        self,
        state_repo: Any,
        workflow_repo: Any,
        submit_uc: Any,
        handle_error: Any,
        tracer: Any,
        transition: Any,
        dispatch_events_from_side_effects: Any,
    ):
        self._state_repo = state_repo
        self._workflow_repo = workflow_repo
        self._submit_uc = submit_uc
        self._handle_error = handle_error
        self._tracer = tracer
        self._transition = transition
        self._dispatch_events_from_side_effects = dispatch_events_from_side_effects

    def submit(
        self,
        ctx: ExecutionContext,
        fields: dict[str, Any] | None = None,
    ) -> dict:
        """Ejecuta el pipeline completo de submit."""
        import time
        _start_ts = time.time()

        # ── Capturar sub-estado ORIGEN antes de ejecutar (puede auto-avanzar) ─
        _before_state = self._state_repo.load(ctx.chain_dir)
        _orig_node = _before_state.current_node
        _orig_sub = _before_state.current_sub_state
        _orig_workflow = self._workflow_repo.load(ctx.chain_dir)
        _orig_node_def = _orig_workflow.get_node(_orig_node)
        _orig_sub_def = _orig_node_def.find_sub_state(_orig_sub) if _orig_node_def else None
        _orig_exec_type = _orig_sub_def.execution.value if _orig_sub_def else "manual"
        _orig_action = _orig_sub_def.action if _orig_sub_def else ""

        # ── Ejecutar submit (TASK-009: try/except con estrategia de error) ──
        from ..domain.errors import WorkflowEngineError
        try:
            result = self._submit_uc.execute(ctx, fields=fields or {})
        except WorkflowEngineError as e:
            duration_ms = int((time.time() - _start_ts) * 1000)
            result = self._handle_error(ctx, e, None, fields=fields or {})
            # Persistir contexto de error (ELM: state inmutable)
            state = self._state_repo.load(ctx.chain_dir)
            state = state.with_context("last_error", result.get("error_type", "unknown"))
            state = state.with_context("last_error_strategy", result.get("strategy", "none"))
            state = state.record_node_execution(
                node=_orig_node,
                sub_state=_orig_sub,
                execution_type=_orig_exec_type,
                action=_orig_action,
                submitted_fields=fields or {},
                task_ok=False,
                error=result.get("message", str(e)),
                error_type=result.get("error_type", "unknown"),
                duration_ms=duration_ms,
            )
            self._state_repo.save(ctx.chain_dir, state)

            # ── Trazar error ───────────────────────────────────────
            self._tracer.trace_error(
                ctx.chain_dir,
                result.get("error_type", "unknown"),
                result.get("message", ""),
                task_id=ctx.task_id,
            )
            return result

        # ── Cargar state para tracing (puede haber auto-avanzado) ────
        duration_ms = int((time.time() - _start_ts) * 1000)
        state = self._state_repo.load(ctx.chain_dir)

        # ── Teoría de grafos: XOR-split resuelto por intent ─────────
        if result.get("_auto_transition") and result.get("_gate"):
            try:
                trans_result = self._transition(ctx, result["_gate"])
                result["advanced_to"] = trans_result.get("to_sub_state", "")
                result["xor_next"] = (
                    f"{trans_result.get('to_node','')}.{trans_result.get('to_sub_state','')}"
                )
                result["xor_done"] = True
                # La transicion persiste el nuevo estado; recargar para
                # no sobrescribirlo con el node_execution posterior.
                state = self._state_repo.load(ctx.chain_dir)
            except Exception as e:
                result["xor_error"] = str(e)

        # ── Registrar NodeExecution (ELM: state inmutable) ─
        state = state.record_node_execution(
            node=_orig_node,
            sub_state=_orig_sub,
            execution_type=_orig_exec_type,
            action=_orig_action,
            submitted_fields=fields or {},
            task_ok=result.get("task_executed", False),
            task_result=result.get("task_result", ""),
            side_effects=result.get("side_effects", []),
            duration_ms=duration_ms,
        )
        self._state_repo.save(ctx.chain_dir, state)

        # ── Trazar submit con decision/evidence ──────────────────────
        decision = fields.get("decision", "") if fields else ""
        evidence = fields.get("evidence", "") if fields else ""
        self._tracer.trace_submit(
            ctx.chain_dir,
            f"{state.current_node}.{state.current_sub_state}",
            decision,
            evidence,
            task_id=ctx.task_id,
        )

        # ── Trazar auto-advance si ocurrio ──────────────────────────
        advanced_to = result.get("advanced_to", "")
        if advanced_to:
            self._tracer.trace(
                ctx.chain_dir,
                "AUTO_ADVANCE",
                data={"from": state.current_sub_state, "to": advanced_to},
                task_id=ctx.task_id,
                session_id=ctx.session_id,
            )

        # ── Trazar side-effects ejecutados ──────────────────────────
        side_effects = result.get("side_effects", [])
        for se in side_effects:
            self._tracer.trace(
                ctx.chain_dir,
                "SIDE_EFFECT",
                data={
                    "type": se.get("type", "?"),
                    "ok": se.get("ok", False),
                    "result": se.get("result", {}),
                },
                task_id=ctx.task_id,
                session_id=ctx.session_id,
            )

        # ── Disparar eventos on:{} por side-effects emit_event ──
        event_count = self._dispatch_events_from_side_effects(ctx, result)
        if event_count > 0:
            self._tracer.trace(
                ctx.chain_dir,
                "EVENT_DISPATCH",
                data={"events_dispatched": event_count},
                task_id=ctx.task_id,
                session_id=ctx.session_id,
            )

        return result
