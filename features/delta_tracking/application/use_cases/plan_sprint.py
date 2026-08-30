"""use_cases/plan_sprint.py — PlanSprintUseCase.

Crea un nuevo sprint: spring + specs en el delta (fuente unica).
PRINCIPIO estructural: el DeltaGraph (SpringService) es la unica
fuente de verdad del spring. plan.json NO existe como fuente paralela
(ADR-WALKER-013). El estado del motor (.workflow-state.json) lo crea
el engine.
"""
from __future__ import annotations

import os

from shared.common.shared_domain import ExecutionContext
from ._sprint_helpers import (
    _resolve_chain_dir,
    _get_active_record,
    _resolve_project_root,
    _resolve_project_root_from_chain,
)


class PlanSprintUseCase:
    """Crea un nuevo sprint: spring + specs en el delta (fuente unica)."""

    @staticmethod
    def _from_payload(data: dict, key: str, default=None):
        """Lee un campo desde _payload (REST body) o desde data directa."""
        payload = data.get("_payload")
        if isinstance(payload, dict) and key in payload:
            return payload.get(key, default)
        return data.get(key, default)

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        goal = PlanSprintUseCase._from_payload(data, "goal", "")
        specs_raw = PlanSprintUseCase._from_payload(
            data, "tasks", PlanSprintUseCase._from_payload(data, "specifications", [])
        )

        sprint_dir = _resolve_chain_dir(
            ctx, project_id=data.get("_project_id", ""), goal=goal
        )

        sprint_id = os.path.basename(sprint_dir)
        record = _get_active_record(ctx)
        # PRIMERO el record de sesión; FALLBACK al _project_id del payload
        # (flujo MCP/CLI sin sesión activa). ANTES: sin record, project_id
        # quedaba vacío → _resolve_project_root("") = "" → el fallback
        # dirname(dirname(sprint_dir)) escribía el delta en
        # cog/data/data/ en vez de cog/data/ (doble segmento 'data') —
        # plan_sprint escribía en un CognitiveGraph distinto al que lee
        # spring_service(chain_dir) → specs invisibles para el lector.
        project_id = (
            (record.project_id if record else "")
            or data.get("_project_id", "")
        )

        # ── Fuente unica: crear spring + specs en el delta ────────────
        from features.delta_tracking import SpringService

        project_root = _resolve_project_root(project_id) or _resolve_project_root_from_chain(
            sprint_dir
        )
        spring_svc = SpringService(project_root)
        spring_svc.create_spring(
            sprint_id,
            goal=goal,
            # El sprint del proyecto está ACTIVO desde su planificación.
            # ANTES se creaba con status="planning": find_active_sprint
            # (que resuelve el chain_dir en CADA submit/transition del
            # MCP/CLI) solo ve status="active" → el siguiente submit tras
            # plan_sprint fallaba con 404 "No active sprint found". La
            # fase del workflow (PLANNING → PLAN_APPROVAL → ...) la lleva
            # el workflow-state; el status del sprint no debe degradarse.
            status="active",
            project_id=project_id,
            session_id=ctx.session_id or "",
        )
        plan_specs = []
        # Batch upsert via SpringService (1 serializacion, no N)
        spring_svc.upsert_specs_batch(
            sprint_id,
            [
                {
                    "objective": item.get("objective", ""),
                    "domain": item.get("domain", ""),
                    "type": item.get("type", "feature"),
                    "result_state": item.get("result_state", ""),
                    "files": (
                        item.get("impacted_artifacts", {}).get("files", [])
                        if isinstance(item.get("impacted_artifacts"), dict)
                        else []
                    ),
                    "status": item.get("status", "draft"),
                    "description": item.get("task_description", ""),
                }
                for item in specs_raw
            ],
        )
        plan_specs = list(specs_raw)

        # Sincronizar .workflow-state.json (canonico del engine) con el
        # estado inicial del sprint (PLANNING). El motor no lee
        # sprint-state.json: el estado de negocio vive en el delta.
        # El host escribe el estado del workflow VÍA el puerto IWorkflowStore
        # (composition root), no construyendo repos concretos.
        try:
            from shared.core.engine.domain.ports_registry import resolve_port
            from shared.core.engine.domain.ports import IWorkflowStore
            from shared.core.engine.domain.entities import WorkflowState

            store = resolve_port(IWorkflowStore)
            if store is not None:
                wf = WorkflowState(current_node="SPRINT", current_sub_state="PLANNING")
                wf.context["chain_dir"] = sprint_dir
                wf.context["project_id"] = project_id
                wf.context["session_id"] = ctx.session_id or ""
                store.save_state(wf, sprint_dir)
        except Exception:
            pass

        if record:
            record.chain_dir = sprint_dir
            from shared.core.engine.domain.ports_registry import resolve_port as _rp
            from shared.core.engine.domain.ports import ISessionStore

            session_store = _rp(ISessionStore)
            if session_store is not None:
                session_store.save(record)
                session_store.set_active(record.session_id)

        # Keep 'tasks' in response for backward compat with workflow prompts
        return {
            "sprint_id": sprint_id,
            "goal": goal,
            "spec_count": len(plan_specs),
            "task_count": len(plan_specs),
            "chain_dir": sprint_dir,
            "status": "planned",
        }
