"""application/transition_orchestrator.py — TransitionOrchestrator.

SRP: Unica responsabilidad = ejecutar transiciones entre nodos del
workflow (gates), con validacion de pre/post contracts, registro de
NodeExecution (saga log), tracing, edge transforms y eventos on:{}.

Extraido de OrchestrationService (god class). El OrchestrationService
queda como fachada y delega aqui.
"""
from __future__ import annotations

import os
import time
from typing import Any

from ..domain.ports import ITracer


class TransitionOrchestrator:
    """Orquesta una transicion por gate del workflow."""

    def __init__(
        self,
        state_repo: Any,
        transition_uc: Any,
        tracer: ITracer,
        read_edge_transform: Any,
        execute_edge_transform: Any,
        dispatch_event: Any,
        workflow_repo: Any = None,
    ):
        self._state_repo = state_repo
        self._transition_uc = transition_uc
        self._tracer = tracer
        self._read_edge_transform = read_edge_transform
        self._execute_edge_transform = execute_edge_transform
        self._dispatch_event = dispatch_event
        self._workflow_repo = workflow_repo

    def transition(
        self,
        ctx: Any,
        gate: str,
        reason: str = "",
        skip_preconditions: bool = False,
    ) -> dict:
        """Transiciona entre nodos con validación de pre/post contracts.

        ANTES: lee el transform del edge (si existe).
        DESPUES: ejecuta transform + eventos on:{}.
        """
        _start_ts = time.time()

        # Capturar nodo origen antes de la transicion
        state_before = self._state_repo.load(ctx.chain_dir)
        from_node = f"{state_before.current_node}.{state_before.current_sub_state}"
        from_sub_state = state_before.current_sub_state

        # ── P1: Resolver bloqueos antes de transicionar ─────────────
        if self._workflow_repo and gate == "escalate":
            workflow = self._workflow_repo.load(ctx.chain_dir)
            from features.engine.domain.services.block_resolver import BlockResolver
            resolve_result = BlockResolver.resolve(
                state_before,
                "escalate",
                {"reason": reason or "transition escalate"},
                graph=workflow,
            )
            from returns.pipeline import is_successful
            if is_successful(resolve_result):
                new_state = resolve_result.value_or(state_before)
                self._state_repo.save(ctx.chain_dir, new_state)
                self._tracer.trace(
                    ctx.chain_dir,
                    "escalate_resolved",
                    data={
                        "from_node": state_before.current_node,
                        "from_sub_state": state_before.current_sub_state,
                        "to_node": new_state.current_node,
                        "to_sub_state": new_state.current_sub_state,
                        "reason": reason or "transition escalate",
                    },
                    task_id=getattr(ctx, "task_id", None),
                    session_id=getattr(ctx, "session_id", None),
                )
                return {
                    "ok": True,
                    "action": "escalated",
                    "from_node": state_before.current_node,
                    "from_sub_state": state_before.current_sub_state,
                    "to_node": new_state.current_node,
                    "to_sub_state": new_state.current_sub_state,
                    "gate": gate,
                    "duration_ms": 0,
                }

        # ── Leer transform ANTES de la transicion (source node) ──
        transform_config = self._read_edge_transform(ctx, gate)

        # ctx positional para que execute() lo reconozca como chain_dir
        result = self._transition_uc.execute(
            ctx,
            gate_name=gate,
            reason=reason,
            skip_preconditions=skip_preconditions,
        )

        # ── Registrar NodeExecution para la gate (ELM/Saga Log) ──────
        duration_ms = int((time.time() - _start_ts) * 1000)
        state = self._state_repo.load(ctx.chain_dir)
        state = state.record_node_execution(
            node=state_before.current_node,
            sub_state=from_sub_state,
            execution_type="transition",
            gate=gate,
            submitted_fields={"reason": reason} if reason else {},
            task_ok=result.get("ok", False),
            task_result={
                "from_node": state_before.current_node,
                "to_node": result.get("to_node", ""),
                "to_sub_state": result.get("to_sub_state", ""),
            },
            duration_ms=duration_ms,
        )
        self._state_repo.save(ctx.chain_dir, state)

        # ── Trazar transicion ────────────────────────────────────────
        to_node = result.get("to_node", "") or result.get("to", "")
        self._tracer.trace_transition(
            ctx.chain_dir,
            gate,
            from_node,
            to_node,
            task_id=ctx.task_id,
        )

        # ── Trazar cambio de estado del workflow (sprint/proyecto) ──
        to_sub_state = result.get("to_sub_state", "")
        try:
            self._tracer.trace(
                ctx.chain_dir,
                "workflow_state",
                data={
                    "from": from_node,
                    "to": f"{to_node}.{to_sub_state}" if to_sub_state else to_node,
                    "gate": gate,
                    "project_id": getattr(ctx, "project_id", ""),
                    "sprint_id": os.path.basename(ctx.chain_dir) if ctx.chain_dir else "",
                },
                task_id=ctx.task_id,
                session_id=ctx.session_id,
            )
        except Exception:
            pass

        # ── Ejecutar edge transform si existe (DESPUES de transition) ─
        if transform_config:
            transform_result = self._execute_edge_transform(
                ctx,
                transform_config,
            )
            if transform_result:
                result["transform"] = transform_result

        # ── Disparar eventos on:{} correlacionados con la gate ──
        self._dispatch_event(ctx, gate, result)

        return result
