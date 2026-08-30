"""application/submit_orchestrator.py — SubmitOrchestrator.

SRP: Unica responsabilidad = ejecutar el pipeline de submit del workflow.

Pipeline:
  1. SubmitDataUseCase.execute() (valida input, ejecuta task, side-effects,
     auto-advance)
  2. Manejo de errores con estrategia (retry/block/compensate/escalate)
  3. Teoria de grafos: XOR-split resuelto por intent (auto-transition)
  4. Registro NodeExecution (saga log) + tracing + eventos on:{}

Refactorizado para usar Result pattern (dry-python/returns) internamente.
El API externo sigue retornando dicts para compatibilidad.
"""

from __future__ import annotations

import time
from typing import Any

from returns.result import safe
from returns.pipeline import is_successful

from shared.common.shared_domain import ExecutionContext
from ..domain.ports import ITracer
from ..domain.events import WorkflowEvent


class SubmitOrchestrator:
    """Orquesta el pipeline de submit de datos al sub-estado actual."""

    def __init__(
        self,
        workflow_store: Any,
        submit_uc: Any,
        handle_error: Any,
        tracer: ITracer,
        transition: Any,
        dispatch_events_from_side_effects: Any,
        signal_bus: Any = None,
    ):
        self._workflow_store = workflow_store
        self._submit_uc = submit_uc
        self._handle_error = handle_error
        self._tracer = tracer
        self._transition = transition
        self._dispatch_events_from_side_effects = dispatch_events_from_side_effects
        self._signal_bus = signal_bus

    def _check_required_tools_gate(self, ctx: Any, sub_state: Any) -> dict:
        """Verifica el gate de auto-consulta (required_tools) antes del submit."""
        required = [t for t in (sub_state.required_tools or []) if isinstance(t, dict)]
        layers = {t.get("id", "") for t in required if t.get("id")}
        if not layers:
            return {"ok": True}

        @safe
        def _check_trace() -> bool:
            return self._tracer.has_kg_query(
                ctx.chain_dir,
                task_id=ctx.task_id or "",
                session_id=ctx.session_id or "",
                layers=layers,
            )

        result = _check_trace()
        if is_successful(result) and result.value_or(False):
            return {"ok": True, "layers_checked": sorted(layers)}

        # Fail-open en errores del tracer
        if not is_successful(result):
            return {"ok": True, "warn": f"trace check failed: {result.failure()}"}

        # Intentar resolver con BlockResolver si el sub-estado está bloqueado
        try:
            from shared.core.engine.domain.services.block_resolver import BlockResolver
            state = self._workflow_store.load_state(ctx.chain_dir)
            if BlockResolver.is_blocked(state):
                # Check if this event can unblock
                if BlockResolver.can_unblock(state, "kg_query"):
                    result = BlockResolver.resolve(state, "force", {
                        "sub_state": state.current_sub_state,
                        "reason": "kg_query evidence found",
                    })
                    if is_successful(result):
                        new_state = result.value_or(state)
                        self._workflow_store.save_state(new_state, ctx.chain_dir)
                        self._tracer.trace_event(
                            WorkflowEvent.of(
                                "force_resolved",
                                payload={
                                    "sub_state": state.current_sub_state,
                                    "node": state.current_node,
                                    "reason": "kg_query evidence found",
                                },
                                task_id=ctx.task_id,
                                session_id=ctx.session_id,
                                chain_dir=ctx.chain_dir,
                            )
                        )
                        return {"ok": True, "layers_checked": sorted(layers), "forced": True, "conflict_resolved": True}
        except Exception as e:
            # Best-effort visible: si el force-resolve falla, la gate bloquea
            # abajo (fail-closed) — el fallo NO se silencia (ADR-067 F6/K).
            import logging

            logging.getLogger(__name__).error(
                "required_tools: force-resolve falló (%s) — la gate bloquea", e
            )

        hints = "; ".join(
            f"{t.get('id', '?')}: {t.get('command_hint', '')}" for t in required
        )
        return {
            "ok": False,
            "action": "gate_blocked",
            "blocked": True,
            "error_type": "required_tools_gate",
            "strategy": "block",
            "message": (
                "GATE DE AUTO-CONSULTA: este paso requiere consultar las "
                f"siguientes herramientas antes de submit — {hints}. "
                "El trace no registra kg_query para esta tarea/sesión. "
                "Invoca la herramienta y vuelve a submit."
            ),
        }

    def submit(
        self,
        ctx: ExecutionContext,
        fields: dict[str, Any] | None = None,
    ) -> dict:
        """Ejecuta el pipeline completo de submit."""
        _start_ts = time.time()

        # ── Capturar sub-estado ORIGEN antes de ejecutar ──────────────
        _before_state = self._workflow_store.load_state(ctx.chain_dir)
        _orig_node = _before_state.current_node
        _orig_sub = _before_state.current_sub_state
        _orig_workflow = self._workflow_store.load_graph()
        _orig_node_def = _orig_workflow.get_node(_orig_node)
        _orig_sub_def = (
            _orig_node_def.find_sub_state(_orig_sub) if _orig_node_def else None
        )
        _orig_exec_type = _orig_sub_def.execution.value if _orig_sub_def else "manual"
        _orig_action = _orig_sub_def.action if _orig_sub_def else ""

        # ── GATE: required_tools ─────────────────────────────────────
        if _orig_sub_def and _orig_sub_def.required_tools:
            gate_check = self._check_required_tools_gate(ctx, _orig_sub_def)
            if not gate_check.get("ok", False):
                self._trace_gate_blocked(ctx, _orig_sub, _orig_sub_def)
                gate_check.update(
                    {
                        "execution_type": _orig_exec_type,
                        "sub_state": _orig_sub,
                        "required_tools": _orig_sub_def.required_tools,
                    }
                )
                return gate_check

        # ── GATES de negocio (p.ej. "trabajo sin capturar" al salir de
        # SPRINT) son INCUMBENCIA DEL HOST (ADR-WALKER-067): el engine solo
        # ejecuta la mecánica del workflow y NO conoce esas reglas. El host
        # valida externamente (antes/después de llamar a submit/transition) y
        # decide si bloquear. No hay lógica de gates de negocio en el engine.

        # ── Ejecutar submit con @safe ────────────────────────────────
        @safe
        def _execute_submit() -> dict:
            return self._submit_uc.execute(ctx, fields=fields or {})

        submit_result = _execute_submit()

        if not is_successful(submit_result):
            # Error capturado por @safe
            error = submit_result.failure()
            return self._handle_submit_error(
                ctx,
                error,
                _start_ts,
                fields or {},
                _orig_node,
                _orig_sub,
                _orig_exec_type,
                _orig_action,
            )

        result = submit_result.value_or({})

        # ── Superficie fallos de side-effects MANDATORIOS al agente ───────────
        # SPEC-014: los optional=True fallan con warning y NO fallan el submit;
        # solo los mandatorios (optional=False) elevan el error.
        _failed_ses = [
            se
            for se in result.get("side_effects", [])
            if isinstance(se, dict)
            and se.get("ok") is False
            and not se.get("optional")
        ]
        if _failed_ses:
            summaries = [
                se.get("summary", se.get("error", se.get("result", "unknown failure")))
                for se in _failed_ses
                if isinstance(se, dict)
            ]
            errors = [
                e
                for se in _failed_ses
                if isinstance(se, dict)
                for e in se.get("errors", [])
            ]
            result["ok"] = False
            result["error"] = "Side-effect(s) fallaron: " + "; ".join(summaries)
            if errors:
                result["errors"] = errors

        # ── Cargar state para tracing ────────────────────────────────
        duration_ms = int((time.time() - _start_ts) * 1000)
        state = self._workflow_store.load_state(ctx.chain_dir)

        # ── Teoría de grafos: XOR-split ──────────────────────────────
        state = self._resolve_xor_auto_transition(
            ctx, result, state, _orig_node, _orig_sub, fields or {}
        )

        # ── Registrar NodeExecution ──────────────────────────────────
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
        self._workflow_store.save_state(state, ctx.chain_dir)

        # ── Trazar submit ────────────────────────────────────────────
        self._trace_submit(ctx, state, fields)
        self._trace_auto_advance(ctx, result, _orig_node, _orig_sub, state)
        self._trace_side_effects(ctx, result)
        self._emit_completion_signals(ctx, _orig_node, _orig_sub, state)

        # ── Disparar eventos on:{} ───────────────────────────────────
        event_count = self._dispatch_events_from_side_effects(ctx, result)
        if event_count > 0:
            self._tracer.trace_event(
                WorkflowEvent.of(
                    "EVENT_DISPATCH",
                    payload={"events_dispatched": event_count},
                    task_id=ctx.task_id,
                    session_id=ctx.session_id,
                    chain_dir=ctx.chain_dir,
                )
            )

        return result

    def _trace_gate_blocked(
        self, ctx: ExecutionContext, _orig_sub: str, sub_def: Any
    ) -> None:
        """Traza un bloqueo de gate."""

        @safe
        def _do_trace():
            self._tracer.trace_event(
                WorkflowEvent.of(
                    "GATE_BLOCKED",
                    payload={
                        "reason": "required_tools",
                        "sub_state": _orig_sub,
                        "required": [t.get("id", "") for t in sub_def.required_tools],
                    },
                    task_id=ctx.task_id,
                    session_id=ctx.session_id,
                    chain_dir=ctx.chain_dir,
                )
            )

        _do_trace()  # Best-effort, no falla el submit por tracing

    def _handle_submit_error(
        self,
        ctx: ExecutionContext,
        e: Exception,
        _start_ts: float,
        fields: dict,
        _orig_node: str,
        _orig_sub: str,
        _orig_exec_type: str,
        _orig_action: str,
    ) -> dict:
        """Maneja error de submit: estrategia + persistencia + tracing."""
        duration_ms = int((time.time() - _start_ts) * 1000)
        result = self._handle_error(ctx, e, None, fields=fields)
        state = self._workflow_store.load_state(ctx.chain_dir)
        state = state.with_context("last_error", result.get("error_type", "unknown"))
        state = state.with_context(
            "last_error_strategy", result.get("strategy", "none")
        )
        state = state.record_node_execution(
            node=_orig_node,
            sub_state=_orig_sub,
            execution_type=_orig_exec_type,
            action=_orig_action,
            submitted_fields=fields,
            task_ok=False,
            error=result.get("message", str(e)),
            error_type=result.get("error_type", "unknown"),
            duration_ms=duration_ms,
        )
        self._workflow_store.save_state(state, ctx.chain_dir)
        self._tracer.trace_error(
            ctx.chain_dir,
            result.get("error_type", "unknown"),
            result.get("message", ""),
            task_id=ctx.task_id,
        )
        return result

    def _resolve_xor_auto_transition(
        self,
        ctx: ExecutionContext,
        result: dict,
        state: Any,
        _orig_node: str,
        _orig_sub: str,
        fields: dict,
    ) -> Any:
        """Aplica la auto-transicion XOR si el submit resolvio un intent/gate."""
        if not (result.get("_auto_transition") and result.get("_gate")):
            return state

        @safe
        def _do_transition() -> dict:
            return self._transition(ctx, result["_gate"])

        trans_result = _do_transition()
        if is_successful(trans_result):
            trans = trans_result.value_or({})
            result["advanced_to"] = trans.get("to_sub_state", "")
            result["xor_next"] = (
                f"{trans.get('to_node', '')}.{trans.get('to_sub_state', '')}"
            )
            result["xor_done"] = True

            if trans.get("to_node") == "REFINE":
                self._tracer.trace_event(
                    WorkflowEvent.of(
                        "REFINE_ENTRY",
                        payload={
                            "from_node": _orig_node,
                            "from_sub_state": _orig_sub,
                            "gate": result.get("_gate", ""),
                            "decision": fields.get("decision", ""),
                            "intent": fields.get("intent", ""),
                            "target": trans.get("to_sub_state", ""),
                            "reason": (
                                "nodeworkflow incompleto o decision de reject/refine"
                            ),
                        },
                        task_id=ctx.task_id,
                        session_id=ctx.session_id,
                        chain_dir=ctx.chain_dir,
                    )
                )

            state = self._workflow_store.load_state(ctx.chain_dir)
        else:
            result["xor_error"] = str(trans_result.failure())

        return state

    def _trace_submit(
        self, ctx: ExecutionContext, state: Any, fields: dict | None
    ) -> None:
        """Traza el submit con decision/evidence."""
        decision = fields.get("decision", "") if fields else ""
        evidence = fields.get("evidence", "") if fields else ""
        self._tracer.trace_submit(
            ctx.chain_dir,
            f"{state.current_node}.{state.current_sub_state}",
            decision,
            evidence,
            task_id=ctx.task_id,
        )

    def _trace_auto_advance(
        self,
        ctx: ExecutionContext,
        result: dict,
        _orig_node: str,
        _orig_sub: str,
        state: Any,
    ) -> None:
        """Traza el auto-advance."""
        advanced_to = result.get("advanced_to", "")
        if not advanced_to:
            return
        self._tracer.trace_event(
            WorkflowEvent.of(
                "AUTO_ADVANCE",
                payload={
                    "from": _orig_sub,
                    "to": advanced_to,
                    "from_node": _orig_node,
                    "to_node": state.current_node,
                },
                task_id=ctx.task_id,
                session_id=ctx.session_id,
                chain_dir=ctx.chain_dir,
            )
        )

    def _trace_side_effects(self, ctx: ExecutionContext, result: dict) -> None:
        """Traza cada side-effect ejecutado."""
        side_effects = result.get("side_effects", [])
        for se in side_effects:
            self._tracer.trace_event(
                WorkflowEvent.of(
                    "SIDE_EFFECT",
                    payload={
                        "type": se.get("type", "?"),
                        "ok": se.get("ok", False),
                        "result": se.get("result", {}),
                    },
                    task_id=ctx.task_id,
                    session_id=ctx.session_id,
                    chain_dir=ctx.chain_dir,
                )
            )

    def _emit_completion_signals(
        self, ctx: ExecutionContext, node_id: str, sub_state_key: str, state: Any
    ) -> None:
        """P1: Emite señales declaradas en el sub-estado completado."""
        if not self._signal_bus:
            return
        try:
            workflow = self._workflow_store.load_graph()
            node = workflow.get_node(node_id)
            if not node:
                return
            sub_state = node.find_sub_state(sub_state_key)
            if not sub_state:
                return
            for signal_name in sub_state.emits or []:
                notified = self._signal_bus.emit(
                    signal=signal_name,
                    source_node=node_id,
                    source_sub_state=sub_state_key,
                    data={"chain_dir": ctx.chain_dir},
                )
                self._tracer.trace_event(
                    WorkflowEvent.of(
                        "SIGNAL_EMIT",
                        payload={
                            "signal": signal_name,
                            "source": f"{node_id}.{sub_state_key}",
                            "notified": notified,
                        },
                        task_id=ctx.task_id,
                        session_id=ctx.session_id,
                        chain_dir=ctx.chain_dir,
                    )
                )
        except Exception:
            pass
