"""application/orchestrator.py — OrchestrationService: unified entry point.

Collapsed from 4 orchestrators (1,471 LOC) + god class (645 LOC) into
a single service with 4 methods (~250 LOC).

Methods:
    submit(ctx, fields)     → dict  (validate → persist → trace)
    transition(ctx, gate)   → dict  (validate gate → apply → persist → trace)
    status(ctx)             → dict  (read-only snapshot)
    help(ctx)               → dict  (current state + available tools)

Composition root OBLIGATORIO: all dependencies injected via constructor.
No resolve_port(), no service locator, no lazy imports.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from datetime import datetime, timezone
from typing import Any

from returns.pipeline import is_successful

from ..domain.entities import WorkflowGraph, WorkflowState
from ..domain.errors import (
    GateNotFoundError,
    NodeNotFoundError,
    TransitionBlockedError,
    WorkflowEngineError,
)
from ..domain.events import WorkflowEvent
from ..domain.execution import ExecutionContext
from ..domain.services.advance import AdvanceService
from ..domain.services.block_resolver import BlockResolver
from ..domain.services.transition import TransitionService
from ..domain.services.validation import ValidationService
from .bundle import EngineBundle


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrchestrationService:
    """Unified entry point for workflow operations.

    The engine is ANEMIC: it does NOT know who calls it or where
    its dependencies come from. The HOST constructs this service
    via composition.py and injects all dependencies.

    INV-S2-02: __init__ receives all deps; no resolve_port() calls.
    INV-S2-03: every method returns dict, never raises.
    """

    def __init__(
        self,
        bundle: EngineBundle,
    ):
        self._bundle = bundle

    # ── Helpers ─────────────────────────────────────────────────────────

    def _load_graph(self) -> WorkflowGraph | None:
        """Load graph from store. Returns None on error."""
        try:
            return self._bundle.workflow_store.load_graph()
        except Exception:
            return None

    def _load_state(self) -> WorkflowState | None:
        """Load state from store. Returns None on error."""
        try:
            return self._bundle.workflow_store.load_state(self._bundle.chain_dir)
        except Exception:
            return None

    def _save_state(self, state: WorkflowState) -> bool:
        """Persist state. Returns True on success."""
        try:
            self._bundle.workflow_store.save_state(self._bundle.chain_dir, state)
            return True
        except Exception:
            return False

    def _trace(self, event: WorkflowEvent) -> None:
        """Best-effort trace. Never fails."""
        try:
            self._bundle.tracer.trace_event(event)
        except Exception:
            pass

    def _trace_submit(
        self, ctx: ExecutionContext, state: WorkflowState, fields: dict | None
    ) -> None:
        decision = fields.get("decision", "") if fields else ""
        evidence = fields.get("evidence", "") if fields else ""
        self._bundle.tracer.trace_submit(
            self._bundle.chain_dir,
            f"{state.current_node}.{state.current_sub_state}",
            decision,
            evidence,
            task_id=ctx.task_id,
        )

    def _trace_transition(
        self,
        ctx: ExecutionContext,
        gate: str,
        from_node: str,
        to_node: str,
    ) -> None:
        self._bundle.tracer.trace_transition(
            self._bundle.chain_dir,
            gate,
            from_node,
            to_node,
            task_id=ctx.task_id,
        )

    def _resolve_current_prompt(
        self, state: WorkflowState, graph: WorkflowGraph, ctx: Any
    ) -> dict | None:
        """Resuelve el prompt del sub-estado actual (carga dinámica por nodo).

        Si el sub-estado es `execution == llm_task` y hay un prompt_manager
        inyectado, devuelve el prompt LLM (template del nodo). NUNCA levanta:
        devuelve None o un dict con 'error' si no resuelve (no rompe el flujo).
        """
        pm = getattr(self._bundle, "prompt_manager", None)
        if pm is None:
            return None
        node = graph.get_node(state.current_node)
        if node is None:
            return None
        sub = node.find_sub_state(state.current_sub_state)
        if sub is None:
            return None
        exec_type = getattr(sub, "execution", None)
        exec_value = getattr(exec_type, "value", exec_type) if exec_type else ""
        sub_fields = {
            "prompt": getattr(sub, "prompt", "") or "",
            "prompt_id": getattr(sub, "prompt_id", "") or "",
            "on_entry_message": getattr(sub, "on_entry_message", "") or "",
            "required_tools": getattr(sub, "required_tools", None),
        }
        try:
            resolved = pm.resolve_for_sub_state(
                node_id=state.current_node,
                sub_state_key=state.current_sub_state,
                execution=exec_value,
                fields=sub_fields,
                extra_data={"revision": state.revision},
            )
        except Exception as e:
            return {"error": str(e)}
        resolved["node"] = state.current_node
        resolved["sub_state"] = state.current_sub_state
        return resolved

    # ── submit ──────────────────────────────────────────────────────────

    def submit(
        self,
        ctx: ExecutionContext,
        fields: dict[str, Any] | None = None,
    ) -> dict:
        """Execute the submit pipeline for the current sub-state.

        Pipeline:
            1. Load state + graph
            2. Reconcile index (INV-P3-01)
            3. Check required_tools gate
            4. Validate required_fields
            5. Record submit data
            6. Record NodeExecution
            7. Increment revision (optimistic concurrency)
            8. Save state
            9. Trace submit
            10. Return result
        """
        try:
            return self._submit_impl(ctx, fields or {})
        except Exception as e:
            return {"ok": False, "error": str(e), "action": "submit_error"}

    def _submit_impl(
        self, ctx: ExecutionContext, fields: dict[str, Any]
    ) -> dict:
        graph = self._load_graph()
        state = self._load_state()
        if graph is None or state is None:
            return {"ok": False, "error": "Failed to load workflow state"}

        # Auto-initialize: if state has no current_node, set to graph's initial_state
        if not state.current_node and graph.initial_state:
            from dataclasses import replace
            init_node = graph.get_node(graph.initial_state)
            init_sub = init_node.sub_states[0].key if init_node and init_node.sub_states else ""
            state = replace(
                state,
                current_node=graph.initial_state,
                current_sub_state=init_sub,
            )

        # 1. Reconcile index (INV-P3-01)
        state, warnings = state.reconcile_index(graph)

        # 2. Get current sub-state definition
        node = graph.get_node(state.current_node)
        if not node:
            return {
                "ok": False,
                "error": f"Current node '{state.current_node}' not in graph",
            }
        sub_state = node.find_sub_state(state.current_sub_state)
        if not sub_state:
            return {
                "ok": False,
                "error": f"Sub-state '{state.current_sub_state}' not in node '{state.current_node}'",
            }

        # 3. Check required_tools gate
        if sub_state.required_tools:
            gate_check = self._check_required_tools_gate(ctx, sub_state)
            if not gate_check.get("ok", False):
                return gate_check

        # 4. Validate required_fields
        errors = ValidationService.check_transition_preconditions(state, graph, "")
        if errors:
            return {
                "ok": False,
                "error": "Missing required fields",
                "errors": errors,
                "action": "validation_failed",
            }

        # 5. Record submit data
        if fields:
            state = state.record_submit(state.current_sub_state, fields)

        # 6. Execute sub_state.action via TaskRegistry (Sprint 5 — engine anémico)
        task_result, task_ok, task_field = self._execute_action(
            ctx, sub_state, state, fields
        )
        if task_result is not None and not task_ok:
            # Task failed → fail-loud, no auto-advance (G-W6 never empty success).
            error_msg = (
                task_field
                or f"Action '{sub_state.action}' failed"
            )
            state = state.record_node_execution(
                node=state.current_node,
                sub_state=state.current_sub_state,
                execution_type=sub_state.execution.value if sub_state else "manual",
                action=sub_state.action if sub_state else "",
                submitted_fields=fields,
                task_ok=False,
                task_result={"error": error_msg, "action": sub_state.action},
                error=error_msg,
                error_type="task_failed",
            )
            if not self._save_state(state):
                return {"ok": False, "error": "Failed to persist state"}
            return {
                "ok": False,
                "action": "task_failed",
                "error": error_msg,
                "task_action": sub_state.action if sub_state else "",
            }

        # 6b. Record NodeExecution
        state = state.record_node_execution(
            node=state.current_node,
            sub_state=state.current_sub_state,
            execution_type=sub_state.execution.value if sub_state else "manual",
            action=sub_state.action if sub_state else "",
            submitted_fields=fields,
            task_ok=task_ok,
            task_result=task_result,
        )

        # 7. Save state (repository handles CAS + revision bump)
        if not self._save_state(state):
            return {"ok": False, "error": "Failed to persist state"}

        # 9. Trace submit
        self._trace_submit(ctx, state, fields)

        return {
            "ok": True,
            "node": state.current_node,
            "sub_state": state.current_sub_state,
            "revision": state.revision,
            "data": fields,
        }

    def _execute_action(
        self,
        ctx: ExecutionContext,
        sub_state: Any,
        state: WorkflowState,
        fields: dict[str, Any],
    ) -> tuple[Any, bool, str]:
        """Execute sub_state.action via TaskRegistry (Sprint 5).

        Mirrors legacy mcp/shared/core/engine/.../submit.py `_execute_task`:

          - No action → (None, True, "") — nothing to execute, task assumed OK
            (there is no declared work to run).
          - Action set but NO task_registry in bundle → FAIL-LOUD (composition
            error, D1/capa_rout.md): the engine MUST NOT silently advance
            without running the declared action.
          - Action executed → returns (task_result, task_ok, task_error).

        G-W6 anti-hallucination: verify_predicates with no predicates must
        NOT pass as empty-success — the target handler already returns
        ok=False in that case, which we propagate (no auto-advance).
        """
        action = sub_state.action if sub_state else ""
        if not action:
            return None, True, ""

        registry = getattr(self._bundle, "task_registry", None)
        if registry is None:
            # Fail-loud: declared action but no registry (broken composition).
            raise WorkflowEngineError(
                f"Sub-state '{state.current_node}.{state.current_sub_state}' "
                f"declares action='{action}' but the engine has no task_registry "
                f"injected (composition root broken). Inject it via context/setup."
            )

        if not registry.has(action):
            raise WorkflowEngineError(
                f"TaskRegistry: action '{action}' not found for "
                f"'{state.current_node}.{state.current_sub_state}'"
            )

        # Build a duck-typed execution context exposing ONLY the attributes the
        # tasks handlers consume (chain_dir, task_id, session_id). We do NOT
        # import the tasks feature's ExecutionContext here (R1: workflow must
        # not import tasks); the injected registry accepts any context object
        # with those attributes.
        from types import SimpleNamespace

        exec_ctx = SimpleNamespace(
            chain_dir=self._bundle.chain_dir,
            task_id=(ctx.task_id if ctx else "") or action,
            session_id=(ctx.session_id if ctx else "") or "mcp",
        )
        exec_data = {
            "task_id": (ctx.task_id if ctx else "") or action,
            "chain_dir": self._bundle.chain_dir,
            **fields,
        }

        # The bundle.task_registry is a tasks.feature TaskRegistry whose
        # execute returns TaskResult(ok, data, error).
        result = registry.execute(action, exec_ctx, exec_data)
        ok = bool(getattr(result, "ok", True))
        error = getattr(result, "error", "") or ""
        data = getattr(result, "data", None) or {}
        return {"action": action, "data": data}, ok, error

    def _check_required_tools_gate(
        self, ctx: ExecutionContext, sub_state: Any
    ) -> dict:
        """Check if required_tools gate is satisfied via trace."""
        required = [t for t in (sub_state.required_tools or []) if isinstance(t, dict)]
        layers = {t.get("id", "") for t in required if t.get("id")}
        if not layers:
            return {"ok": True}

        # Check trace for recent kg_query
        try:
            has_query = self._bundle.tracer.has_kg_query(
                self._bundle.chain_dir,
                task_id=ctx.task_id or "",
                session_id=ctx.session_id or "",
                layers=layers,
            )
            if has_query:
                return {"ok": True, "layers_checked": sorted(layers)}
        except Exception:
            pass  # fail-open on tracer errors

        # Gate blocked
        hints = "; ".join(
            f"{t.get('id', '?')}: {t.get('command_hint', '')}" for t in required
        )
        return {
            "ok": False,
            "action": "gate_blocked",
            "blocked": True,
            "error_type": "required_tools_gate",
            "message": (
                "This step requires consulting tools before submit: "
                f"{hints}. Invoke the tool and retry submit."
            ),
        }

    # ── transition ──────────────────────────────────────────────────────

    def transition(
        self,
        ctx: ExecutionContext,
        gate: str,
        reason: str = "",
        skip_preconditions: bool = False,
    ) -> dict:
        """Transition between nodes via a gate.

        Pipeline:
            1. Load state + graph
            2. Check if blocked (resolve if possible)
            3. Find gate + validate source
            4. Check preconditions (unless skip)
            5. Apply transition
            6. Record NodeExecution
            7. Save state
            8. Trace transition
            9. Return result
        """
        try:
            return self._transition_impl(ctx, gate, reason, skip_preconditions)
        except Exception as e:
            return {"ok": False, "error": str(e), "action": "transition_error"}

    def _transition_impl(
        self,
        ctx: ExecutionContext,
        gate: str,
        reason: str,
        skip_preconditions: bool,
    ) -> dict:
        graph = self._load_graph()
        state = self._load_state()
        if graph is None or state is None:
            return {"ok": False, "error": "Failed to load workflow state"}

        # 1. Check if blocked — resolve with "force" strategy
        if BlockResolver.is_blocked(state):
            resolve_result = BlockResolver.resolve(
                state, "force", {"reason": reason or "transition"}, graph=graph
            )
            if is_successful(resolve_result):
                state = resolve_result.value_or(state)

        # 2. Find gate
        gate_obj = graph.find_gate(state.current_node, gate)
        if gate_obj is None:
            return {
                "ok": False,
                "error": f"Gate '{gate}' not found for node '{state.current_node}'",
                "action": "gate_not_found",
            }

        # 3. Validate source node
        if gate_obj.source_node != state.current_node:
            return {
                "ok": False,
                "error": (
                    f"Gate '{gate}' source is '{gate_obj.source_node}', "
                    f"but current node is '{state.current_node}'"
                ),
                "action": "invalid_source",
            }

        # 4. Check preconditions (unless skipped)
        if not skip_preconditions:
            pre_errors = ValidationService.check_transition_preconditions(
                state, graph, gate
            )
            if pre_errors:
                return {
                    "ok": False,
                    "error": "Transition preconditions not met",
                    "errors": pre_errors,
                    "action": "precondition_failed",
                }

        # 5. Capture from_node for tracing
        from_node = f"{state.current_node}.{state.current_sub_state}"

        # 6. Apply transition
        try:
            state = TransitionService.apply(
                state,
                graph,
                gate_obj.target_node,
                gate_obj.target_sub_state,
            )
        except NodeNotFoundError as e:
            return {"ok": False, "error": str(e), "action": "target_not_found"}

        # 7. Record NodeExecution
        state = state.record_node_execution(
            execution_type="transition",
            gate=gate,
            submitted_fields={"reason": reason} if reason else {},
            task_ok=True,
            task_result={
                "from_node": gate_obj.source_node,
                "to_node": gate_obj.target_node,
                "to_sub_state": gate_obj.target_sub_state or "",
            },
        )

        # 8. Save state (repository handles CAS + revision bump)
        if not self._save_state(state):
            return {"ok": False, "error": "Failed to persist state"}

        # 10. Trace transition
        to_node = f"{state.current_node}.{state.current_sub_state}"
        self._trace_transition(ctx, gate, from_node, to_node)

        return {
            "ok": True,
            "from_node": gate_obj.source_node,
            "to_node": gate_obj.target_node,
            "to_sub_state": state.current_sub_state,
            "gate": gate,
            "revision": state.revision,
        }

    # ── status ──────────────────────────────────────────────────────────

    def status(self, ctx: ExecutionContext) -> dict:
        """Read-only snapshot of the current workflow state.

        INV-S2-06: never mutates state.
        """
        graph = self._load_graph()
        state = self._load_state()
        if graph is None or state is None:
            return {"ok": False, "error": "Failed to load workflow state"}

        node = graph.get_node(state.current_node)
        sub_state_def = node.find_sub_state(state.current_sub_state) if node else None

        # Available gates
        available_gates = []
        if node:
            gates = ValidationService.get_available_gates(state, graph)
            for g in gates:
                available_gates.append({
                    "name": g.name,
                    "target": f"{g.target_node}.{g.target_sub_state or '*'}"
                    if g.target_sub_state
                    else g.target_node,
                    "description": g.description,
                })

        # Last 10 executions
        last_executions = []
        for ne in state.node_executions[-10:]:
            last_executions.append({
                "node": ne.node,
                "sub_state": ne.sub_state,
                "execution_type": ne.execution_type,
                "action": ne.action,
                "task_ok": ne.task_ok,
                "gate": ne.gate,
                "error": ne.error,
                "duration_ms": ne.duration_ms,
            })

        return {
            "ok": True,
            "node": state.current_node,
            "sub_state": state.current_sub_state,
            "sub_state_index": state.sub_state_index,
            "execution_type": sub_state_def.execution.value
            if sub_state_def
            else "unknown",
            "required_fields": list(sub_state_def.required_fields)
            if sub_state_def
            else [],
            "history": list(state.history),
            "available_gates": available_gates,
            "node_executions": last_executions,
            "revision": state.revision,
            "blocked": bool(state.blocked_at),
            "task_id": ctx.task_id,
            "session_id": ctx.session_id,
            "prompt": self._resolve_current_prompt(state, graph, ctx),
        }

    # ── help ────────────────────────────────────────────────────────────

    def help(self, ctx: ExecutionContext) -> dict:
        """Current state summary + available operations.

        Read-only: INV-S2-06.
        """
        graph = self._load_graph()
        state = self._load_state()
        if graph is None or state is None:
            return {"ok": False, "error": "Failed to load workflow state"}

        node = graph.get_node(state.current_node)
        sub_state_def = node.find_sub_state(state.current_sub_state) if node else None

        # Available gates
        available_gates = []
        if node:
            gates = ValidationService.get_available_gates(state, graph)
            for g in gates:
                available_gates.append({
                    "name": g.name,
                    "target": g.target_node,
                    "description": g.description,
                })

        # Required fields for current sub-state
        required_fields = list(sub_state_def.required_fields) if sub_state_def else []

        # Execution type guidance
        exec_type = sub_state_def.execution.value if sub_state_def else "unknown"

        return {
            "ok": True,
            "current_position": f"{state.current_node}.{state.current_sub_state}",
            "execution_type": exec_type,
            "required_fields": required_fields,
            "available_gates": [g["name"] for g in available_gates],
            "gates_detail": available_gates,
            "history": list(state.history[-5:]),
            "blocked": bool(state.blocked_at),
            "revision": state.revision,
            "prompt": self._resolve_current_prompt(state, graph, ctx),
        }
