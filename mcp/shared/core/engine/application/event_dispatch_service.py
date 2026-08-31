"""application/event_dispatch_service.py — EventDispatchService (SRP, ADR-WALKER-067 F3/C).

Responsabilidad: despachar eventos on:{} del workflow tras ejecutar
side-effects. Consume puertos (workflow_store, actions) + un provider del
TransitionUseCase (evita acoplar el servicio al lazy-init del facade).
"""
from __future__ import annotations

from typing import Any, Callable

from ..domain.models import ExecutionContext


class EventDispatchService:
    """Despacho de eventos on:{} definidos en workflow.json.

    SRP: única responsabilidad — escanear side-effects del resultado de
    submit() y ejecutar handlers de eventos (run → action, transition → gate).
    """

    def __init__(
        self,
        workflow_store: Any,
        actions: Any,
        transition_uc_provider: Callable[[], Any],
        event_dispatcher: Any = None,
        event_log: Any = None,
        tracer: Any = None,
    ):
        self._workflow_store = workflow_store
        self._actions = actions
        self._transition_uc_provider = transition_uc_provider
        self._event_dispatcher = event_dispatcher
        self._event_log = event_log
        self._tracer = tracer

    def dispatch_events_from_side_effects(
        self,
        ctx: ExecutionContext,
        result: dict,
    ) -> int:
        """Escanea side-effects en el resultado de submit() y dispara eventos on:{}.

        Por cada side-effect type=emit_event, busca el evento en
        workflow.json 'on' y ejecuta su handler (run o transition).

        ADEMÁS: consume el CONTRATO ESTÁNDAR de side-effects. Si un side-effect
        reporta quality_passed=False (signals.recommend='refactorizar_primero'),
        dispara el evento estándar 'quality_gate_failed' que el workflow puede
        declarar en on:{} para desviar a REFINE automáticamente.

        Returns:
            Numero de eventos despachados.
        """
        side_effects = result.get("side_effects", [])
        if not side_effects:
            return 0

        count = 0
        for se in side_effects:
            se_type = se.get("type")
            se_result = se.get("result", {}) or {}
            signals = (
                se_result.get("signals", {}) if isinstance(se_result, dict) else {}
            )

            # 1. Eventos explícitos (emit_event). El nombre del evento puede
            #    estar en signals (envelope estándar) o en result.event (legacy).
            if se_type == "emit_event":
                if isinstance(se_result, dict):
                    event_name = (
                        signals.get("event")
                        or se_result.get("event")
                        or (se_result.get("metadata", {}) or {}).get("event", "")
                    )
                else:
                    event_name = (
                        se_result.get("event", "")
                        if isinstance(se_result, dict)
                        else ""
                    )
                if event_name:
                    dispatch_result = self.dispatch_event(ctx, event_name, result)
                    if dispatch_result:
                        count += 1
                continue

            # 2. Contrato estándar: quality gate falló → evento estándar
            if isinstance(se_result, dict) and se_result.get("scope") == "code_index":
                if signals.get("quality_passed") is False:
                    summary = se_result.get("summary", "quality gates fallaron")
                    dispatch_result = self.dispatch_event(
                        ctx,
                        "quality_gate_failed",
                        result,
                    )
                    if dispatch_result:
                        count += 1
                    # Adjuntar la info de calidad al resultado para el agente
                    result.setdefault("quality", {})["last_scan"] = {
                        "summary": summary,
                        "failed_gates": signals.get("failed_gate_names", []),
                        "recommend": signals.get("recommend", ""),
                        "degraded": se_result.get("status") == "degraded",
                    }
        return count

    def dispatch_event(
        self,
        ctx: ExecutionContext,
        event_name: str,
        result: dict | None = None,
    ) -> dict | None:
        """Busca y ejecuta el handler de un evento on:{}.

        Los eventos se definen en workflow.json on:{} con dos tipos:
          - {"run": "action_name"}: Ejecuta accion via TaskRegistry
          - {"transition": "gate_name"}: Aplica transicion

        Args:
            ctx: ExecutionContext actual.
            event_name: Nombre del evento (ej: "task_approved").
            result: Resultado previo para metadata.

        Returns:
            Resultado del handler o None si no hay handler.
        """
        workflow = self._workflow_store.load_graph()
        handler = workflow.find_event_handler(event_name)
        if not handler:
            return None

        # Metadata del evento (incluye nodo actual real)
        state = self._workflow_store.load_state(ctx.chain_dir)
        metadata = {
            "event": event_name,
            "task_id": ctx.task_id,
            "node": f"{state.current_node}.{state.current_sub_state}",
            "timestamp": ctx.started_at,
            "session_id": ctx.session_id,
        }

        # ── Handler type: run (via TaskRegistry) ────────────────────
        if "run" in handler:
            action = handler["run"]
            try:
                exec_ctx = ExecutionContext.create(
                    session_id=ctx.session_id,
                    chain_dir=ctx.chain_dir,
                    task_id=ctx.task_id,
                    trace_id=ctx.trace_id,
                )
                task_result = self._actions.execute_action(action, exec_ctx, metadata)
                return {
                    "event": event_name,
                    "handler_type": "run",
                    "action": action,
                    "ok": task_result.ok,
                    "result": str(task_result.data)[:500] if task_result.data else None,
                }
            except Exception as e:
                return {
                    "event": event_name,
                    "handler_type": "run",
                    "action": action,
                    "ok": False,
                    "error": str(e),
                }

        # ── Handler type: transition (via TransitionUseCase) ────────
        elif "transition" in handler:
            gate = handler["transition"]
            try:
                transition_result = self._transition_uc_provider().execute(
                    ctx,
                    gate_name=gate,
                )
                return {
                    "event": event_name,
                    "handler_type": "transition",
                    "gate": gate,
                    "ok": True,
                    "target": transition_result.get("to_node", ""),
                }
            except Exception as e:
                return {
                    "event": event_name,
                    "handler_type": "transition",
                    "gate": gate,
                    "ok": False,
                    "error": str(e),
                }

        return None