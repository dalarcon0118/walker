"""application/status_orchestrator.py — StatusOrchestrator.

SRP: Unica responsabilidad = construir el snapshot de estado del workflow
para status(). NO muta estado, NO ejecuta transiciones.

Extraido de OrchestrationService (era un god class de 1565 lineas).
El OrchestrationService queda como fachada y delega aqui.
"""

from __future__ import annotations

from typing import Any

from ..domain.models import SubState
from ..infrastructure.repositories import WorkflowRepository, WorkflowStateRepository
from ..domain.services import ValidationService


class StatusOrchestrator:
    """Construye el snapshot de estado del workflow (read-only)."""

    def __init__(
        self,
        workflow_repo: WorkflowRepository,
        state_repo: WorkflowStateRepository,
        resolve_status_prompt: Any,
        validate_uc: Any,
    ):
        self._workflow_repo = workflow_repo
        self._state_repo = state_repo
        self._resolve_status_prompt = resolve_status_prompt
        self._validate_uc = validate_uc

    def status(self, ctx: Any) -> dict:
        """Snapshot del estado actual + workflow info.

        Returns:
            dict con node, sub_state, sub_state_index, execution_type,
            required_fields, prompt, history, node_complete,
            available_gates, validation, task_id, session_id,
            node_executions.
        """
        workflow = self._workflow_repo.load(ctx.chain_dir)
        state = self._state_repo.load(ctx.chain_dir)
        node = workflow.get_node(state.current_node)
        sub_state: SubState | None = None
        if node:
            sub_state = node.find_sub_state(state.current_sub_state)

        # Validar estado
        validation = self._validate_uc.execute(ctx.chain_dir)

        # Gates disponibles (filtradas por nodo Y sub-estado origen)
        available_gates = []
        if node:
            gates = ValidationService.get_available_gates(state, workflow)
            for g in gates:
                available_gates.append(
                    {
                        "name": g.name,
                        "target": f"{g.target_node}.{g.target_sub_state or '*'}"
                        if g.target_sub_state
                        else g.target_node,
                        "description": g.description,
                    }
                )

        # ── Helpers para formato compacto ─────────────────────────
        _MAX_FIELD_VAL = 120  # max chars per submitted_field value
        _MAX_RESULT = 300  # max chars for task_result
        _MAX_FIELDS = 5  # max submitted fields to show

        def _summarize_fields(d: dict) -> dict:
            """Resume submitted_fields: acorta valores largos, limita cantidad."""
            out = {}
            for i, (k, v) in enumerate(d.items()):
                if i >= _MAX_FIELDS:
                    out[f"...+{len(d) - _MAX_FIELDS} more"] = True
                    break
                if isinstance(v, str) and len(v) > _MAX_FIELD_VAL:
                    out[k] = v[:_MAX_FIELD_VAL] + "..."
                elif isinstance(v, (list, dict)):
                    s = str(v)
                    out[k] = (
                        s[:_MAX_FIELD_VAL] + "..." if len(s) > _MAX_FIELD_VAL else v
                    )
                else:
                    out[k] = v
            return out

        def _truncate_result(r) -> str | dict:
            """Trunca task_result: strings largos o dicts con valores largos."""
            if r is None:
                return ""
            if isinstance(r, str):
                return r[:_MAX_RESULT] + "..." if len(r) > _MAX_RESULT else r
            if isinstance(r, dict):
                out = {}
                for k, v in r.items():
                    if isinstance(v, str) and len(v) > _MAX_FIELD_VAL:
                        out[k] = v[:_MAX_FIELD_VAL] + "..."
                    else:
                        out[k] = v
                return out
            if isinstance(r, (list, tuple)):
                s = str(r)
                return s[:_MAX_RESULT] + "..." if len(s) > _MAX_RESULT else s
            return str(r)

        # Últimas ejecuciones (node_executions del sprint-state)
        last_executions = []
        for ne in state.node_executions[-10:]:
            last_executions.append(
                {
                    "node": ne.node,
                    "sub_state": ne.sub_state,
                    "execution_type": ne.execution_type,
                    "action": ne.action,
                    "task_ok": ne.task_ok,
                    "gate": ne.gate,
                    "error": ne.error,
                    "error_type": ne.error_type,
                    "duration_ms": ne.duration_ms,
                    "completed_at": ne.completed_at[11:19]
                    if ne.completed_at and len(ne.completed_at) > 19
                    else (ne.completed_at or ""),
                    "submitted_fields": _summarize_fields(ne.submitted_fields),
                    "task_result": _truncate_result(ne.task_result),
                    "side_effects": [
                        {"type": se.get("type", "?"), "ok": se.get("ok", False)}
                        for se in (ne.side_effects or [])
                    ],
                }
            )

        # ── Teoria de grafos: mapa de progreso (GPS del agente) ──
        try:
            from ..domain.graph_analyzer import GraphAnalyzer

            progress = GraphAnalyzer(workflow).progress_map(state.current_node)
        except Exception:
            progress = {}

        return {
            "node": state.current_node,
            "sub_state": state.current_sub_state,
            "sub_state_index": state.sub_state_index,
            "execution_type": sub_state.execution.value if sub_state else "unknown",
            "required_fields": sub_state.required_fields if sub_state else [],
            "prompt": self._resolve_status_prompt(sub_state, ctx) if sub_state else "",
            "prompt_id": sub_state.prompt_id if sub_state else "",
            "history": list(state.history),
            "node_complete": ValidationService.is_node_complete(state, workflow)
            if node
            else True,
            "available_gates": available_gates,
            "validation": validation,
            "task_id": ctx.task_id,
            "session_id": ctx.session_id,
            "node_executions": last_executions,
            "node_executions_total": len(state.node_executions),
            "graph_progress": progress,
        }
