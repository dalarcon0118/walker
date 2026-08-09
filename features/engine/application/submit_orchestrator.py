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

from libs.shared_domain import ExecutionContext
from ..domain.ports import ITracer


class SubmitOrchestrator:
    """Orquesta el pipeline de submit de datos al sub-estado actual."""

    def __init__(
        self,
        state_repo: Any,
        workflow_repo: Any,
        submit_uc: Any,
        handle_error: Any,
        tracer: ITracer,
        transition: Any,
        dispatch_events_from_side_effects: Any,
        signal_bus: Any = None,
    ):
        self._state_repo = state_repo
        self._workflow_repo = workflow_repo
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
            from features.engine.domain.services.block_resolver import BlockResolver
            from features.engine.infrastructure.repositories import WorkflowStateRepository
            state = WorkflowStateRepository().load(ctx.chain_dir)
            if BlockResolver.is_blocked(state):
                # Check if this event can unblock
                if BlockResolver.can_unblock(state, "kg_query"):
                    result = BlockResolver.resolve(state, "force", {
                        "sub_state": state.current_sub_state,
                        "reason": "kg_query evidence found",
                    })
                    if is_successful(result):
                        new_state = result.value_or(state)
                        self._state_repo.save(new_state, ctx.chain_dir)
                        self._tracer.trace(
                            ctx.chain_dir,
                            "force_resolved",
                            data={
                                "sub_state": state.current_sub_state,
                                "node": state.current_node,
                                "reason": "kg_query evidence found",
                            },
                            task_id=ctx.task_id,
                            session_id=ctx.session_id,
                        )
                        return {"ok": True, "layers_checked": sorted(layers), "forced": True, "conflict_resolved": True}
        except Exception:
            pass

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
        _before_state = self._state_repo.load(ctx.chain_dir)
        _orig_node = _before_state.current_node
        _orig_sub = _before_state.current_sub_state
        _orig_workflow = self._workflow_repo.load(ctx.chain_dir)
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

        # ── GATE: trabajo sin capturar ───────────────────────────────
        _SPRINT_EXIT_SUBSTATES = {"REVIEW", "CLOSE_CONFIRM"}
        if _orig_node == "SPRINT" and _orig_sub in _SPRINT_EXIT_SUBSTATES:
            blocked = self._check_uncaptured_work_gate(
                ctx, _orig_node, _orig_sub, _orig_exec_type
            )
            if blocked:
                return blocked

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

        # ── Superficie fallos de side-effects al agente ──────────────
        _failed_ses = [
            se
            for se in result.get("side_effects", [])
            if isinstance(se, dict) and se.get("ok") is False
        ]
        if _failed_ses:
            summaries = [
                se.get("summary", se.get("error", "unknown failure"))
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
        state = self._state_repo.load(ctx.chain_dir)

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
        self._state_repo.save(ctx.chain_dir, state)

        # ── Trazar submit ────────────────────────────────────────────
        self._trace_submit(ctx, state, fields)
        self._trace_auto_advance(ctx, result, _orig_node, _orig_sub, state)
        self._trace_side_effects(ctx, result)
        self._emit_completion_signals(ctx, _orig_node, _orig_sub, state)

        # ── Disparar eventos on:{} ───────────────────────────────────
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

    def _trace_gate_blocked(
        self, ctx: ExecutionContext, _orig_sub: str, sub_def: Any
    ) -> None:
        """Traza un bloqueo de gate."""

        @safe
        def _do_trace():
            self._tracer.trace(
                ctx.chain_dir,
                "GATE_BLOCKED",
                data={
                    "reason": "required_tools",
                    "sub_state": _orig_sub,
                    "required": [t.get("id", "") for t in sub_def.required_tools],
                },
                task_id=ctx.task_id,
                session_id=ctx.session_id,
            )

        _do_trace()  # Best-effort, no falla el submit por tracing

    def _check_uncaptured_work_gate(
        self, ctx: ExecutionContext, _orig_node: str, _orig_sub: str, exec_type: str
    ) -> dict | None:
        """Bloquea el cierre del sprint si hay cambios sin capturar."""
        from .uncaptured_work import detect_uncaptured_work

        uw = detect_uncaptured_work(ctx.chain_dir)
        if not uw.should_block:
            return None

        @safe
        def _do_trace():
            self._tracer.trace(
                ctx.chain_dir,
                "GATE_BLOCKED",
                data={
                    "reason": "uncaptured_work",
                    "sub_state": _orig_sub,
                    "uncaptured_files": uw.uncaptured_files,
                    "spring_id": uw.spring_id,
                },
                task_id=ctx.task_id,
                session_id=ctx.session_id,
            )

        _do_trace()  # Best-effort

        return {
            "ok": False,
            "action": "gate_blocked",
            "blocked": True,
            "error_type": "uncaptured_work_gate",
            "strategy": "block",
            "message": uw.gate_message(),
            "execution_type": exec_type,
            "sub_state": _orig_sub,
            "uncaptured_files": uw.uncaptured_files,
        }

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
        state = self._state_repo.load(ctx.chain_dir)
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
        self._state_repo.save(ctx.chain_dir, state)
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
                self._tracer.trace(
                    ctx.chain_dir,
                    "REFINE_ENTRY",
                    data={
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
                )

            state = self._state_repo.load(ctx.chain_dir)
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
        self._tracer.trace(
            ctx.chain_dir,
            "AUTO_ADVANCE",
            data={
                "from": _orig_sub,
                "to": advanced_to,
                "from_node": _orig_node,
                "to_node": state.current_node,
            },
            task_id=ctx.task_id,
            session_id=ctx.session_id,
        )

    def _trace_side_effects(self, ctx: ExecutionContext, result: dict) -> None:
        """Traza cada side-effect ejecutado."""
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

    def _emit_completion_signals(
        self, ctx: ExecutionContext, node_id: str, sub_state_key: str, state: Any
    ) -> None:
        """P1: Emite señales declaradas en el sub-estado completado."""
        if not self._signal_bus:
            return
        try:
            workflow = self._workflow_repo.load(ctx.chain_dir)
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
                self._tracer.trace(
                    ctx.chain_dir,
                    "SIGNAL_EMIT",
                    data={
                        "signal": signal_name,
                        "source": f"{node_id}.{sub_state_key}",
                        "notified": notified,
                    },
                    task_id=ctx.task_id,
                    session_id=ctx.session_id,
                )
        except Exception:
            pass
