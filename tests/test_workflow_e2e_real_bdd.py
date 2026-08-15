"""tests/test_workflow_e2e_real_bdd.py — E2E real: ejecuta el workflow engine con API real y mocks.

Usa OrchestrationService.submit() y OrchestrationService.transition()
para recorrer el flujo completo:
  SPRINT(PLANNING → TASK_SPEC → TASK_REVIEW → ACTIVE) →
  DESIGN(GATHER) →
  ANALYZE(GATHER) →
  IMPLEMENT(USER_GATE → WORK → CODE_REVIEW_SCAN → CODE_REVIEW_RESULT → REPORT_GATE) →
  VERIFY(USER_GATE → QA_RUN → PO_ACCEPT) →
  SPRINT(ACTIVE)

Valida en cada paso:
  - WorkflowState evoluciona (current_node, current_sub_state)
  - Side effects se ejecutan (transform_spec, create_adr)
  - Spec evoluciona en DeltaGraph
  - ADR se crea en CKG cuando corresponde
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.engine.application.orchestrator import OrchestrationService
from core.engine.application.use_cases import ScriptRunner
from core.engine.domain.entities import WorkflowState
from core.engine.infrastructure.repositories import (
    WorkflowRepository,
    WorkflowStateRepository,
)
from features.delta_tracking import spring_service
from features.delta_tracking.domain.entities import Spec
from tests.conftest import make_chain_dir, register_mock_tasks


# ── Fixtures y helpers ────────────────────────────────────────────────

def _setup_project(tmp_path: Path) -> tuple[str, str]:
    """Crea la estructura completa del proyecto: project.json, workflow, delta, workflow state."""
    chain_dir = make_chain_dir(tmp_path, "sprint-e2e", project_name="walker")

    _write_workflow(chain_dir)
    _setup_delta(chain_dir)
    _setup_workflow_state(chain_dir)

    return str(tmp_path), chain_dir


def _write_workflow(chain_dir: str) -> None:
    """Escribe un workflow.json simplificado con el flujo completo."""
    workflow = {
        "label": "E2E Workflow",
        "description": "Flujo completo de prueba E2E",
        "initial_state": "SPRINT",
        "nodes": {
            "SPRINT": {
                "sub_states": [
                    {
                        "key": "PLANNING",
                        "label": "Sprint planning",
                        "execution": "llm_task",
                        "action": "plan_sprint",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "goal": {"type": "string"},
                                "tasks": {"type": "array"},
                            },
                            "required": ["goal", "tasks"],
                        },
                        "output_schema": {
                            "type": "object",
                            "properties": {
                                "status": {"type": "string", "enum": ["planned"]},
                                "sprint_id": {"type": "string"},
                            },
                            "required": ["status", "sprint_id"],
                        },
                        "on_success": "TASK_SPEC",
                        "side_effects": [
                            {"type": "transform_spec", "params": {"status": "draft"}, "optional": False},
                        ],
                    },
                    {
                        "key": "TASK_SPEC",
                        "label": "Task specification",
                        "execution": "llm_task",
                        "action": "spec_task",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "task_id": {"type": "string"},
                                "objective": {"type": "string"},
                            },
                            "required": ["task_id"],
                        },
                        "output_schema": {
                            "type": "object",
                            "properties": {
                                "task_id": {"type": "string"},
                                "status": {"type": "string"},
                                "approval_type": {"type": "string", "enum": ["standard", "decision"]},
                                "decision_data": {"type": "object"},
                            },
                            "required": ["task_id", "status"],
                        },
                        "on_success": "TASK_REVIEW",
                        "side_effects": [
                            {"type": "transform_spec", "params": {"status": "specified"}, "optional": False},
                        ],
                    },
                    {
                        "key": "TASK_REVIEW",
                        "label": "Task review",
                        "execution": "llm_task",
                        "action": "review_task",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "decision": {"type": "string", "enum": ["approve", "refine", "all_done"]},
                                "feedback": {"type": "string"},
                            },
                            "required": ["decision"],
                        },
                        "output_schema": {
                            "type": "object",
                            "properties": {
                                "decision": {"type": "string"},
                                "feedback": {"type": "string"},
                                "approval_type": {"type": "string", "enum": ["standard", "decision"]},
                                "approval_data": {"type": "object"},
                            },
                            "required": ["decision"],
                        },
                        "on_success": "DESIGN_GATHER",
                        "side_effects": [
                            {
                                "type": "transform_spec",
                                "params": {
                                    "status": "approved",
                                    "fields": {
                                        "decision": "NODE.TASK_SPEC.output.decision_data",
                                        "architecture": "NODE.TASK_SPEC.output.decision_data",
                                        "implementation": "NODE.TASK_SPEC.output.decision_data",
                                    },
                                },
                                "optional": False,
                            },
                            {
                                "type": "create_adr",
                                "params": {"approval_type": "NODE.TASK_SPEC.output.approval_type"},
                                "optional": True,
                            },
                        ],
                    },
                    {
                        "key": "DESIGN_GATHER",
                        "label": "Design gather",
                        "execution": "llm_task",
                        "action": "gather_design",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                        "on_success": "ANALYZE_GATHER",
                        "side_effects": [
                            {"type": "transform_spec", "params": {"status": "in_progress"}, "optional": False},
                        ],
                    },
                    {
                        "key": "ANALYZE_GATHER",
                        "label": "Analyze gather",
                        "execution": "llm_task",
                        "action": "gather_analysis",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                        "on_success": "IMPLEMENT_USER_GATE",
                        "side_effects": [
                            {"type": "transform_spec", "params": {"status": "in_progress"}, "optional": False},
                        ],
                    },
                    {
                        "key": "IMPLEMENT_USER_GATE",
                        "label": "User gate",
                        "execution": "llm_task",
                        "action": "user_gate",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                        "on_success": "IMPLEMENT_WORK",
                        "side_effects": [
                            {"type": "transform_spec", "params": {"status": "in_progress"}, "optional": False},
                        ],
                    },
                    {
                        "key": "IMPLEMENT_WORK",
                        "label": "Implementation work",
                        "execution": "llm_task",
                        "action": "implement",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                        "on_success": "CODE_REVIEW_SCAN",
                        "side_effects": [
                            {"type": "transform_spec", "params": {"status": "in_progress"}, "optional": False},
                        ],
                    },
                    {
                        "key": "CODE_REVIEW_SCAN",
                        "label": "Code review scan",
                        "execution": "auto",
                        "action": "scan_code",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                        "on_success": "CODE_REVIEW_RESULT",
                        "side_effects": [
                            {"type": "transform_spec", "params": {"status": "in_review"}, "optional": False},
                        ],
                    },
                    {
                        "key": "CODE_REVIEW_RESULT",
                        "label": "Code review result",
                        "execution": "llm_task",
                        "action": "review_code",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                        "on_success": "REPORT_GATE",
                        "side_effects": [
                            {"type": "transform_spec", "params": {"status": "in_review"}, "optional": False},
                        ],
                    },
                    {
                        "key": "REPORT_GATE",
                        "label": "Report gate",
                        "execution": "llm_task",
                        "action": "report_gate",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                        "on_success": "VERIFY_USER_GATE",
                        "side_effects": [
                            {"type": "transform_spec", "params": {"status": "completed"}, "optional": False},
                        ],
                    },
                    {
                        "key": "VERIFY_USER_GATE",
                        "label": "Verify user gate",
                        "execution": "llm_task",
                        "action": "verify_user_gate",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                        "on_success": "QA_RUN",
                        "side_effects": [
                            {"type": "transform_spec", "params": {"status": "completed"}, "optional": False},
                        ],
                    },
                    {
                        "key": "QA_RUN",
                        "label": "QA run",
                        "execution": "llm_task",
                        "action": "qa_run",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                        "on_success": "PO_ACCEPT",
                        "side_effects": [
                            {"type": "transform_spec", "params": {"status": "completed"}, "optional": False},
                        ],
                    },
                    {
                        "key": "PO_ACCEPT",
                        "label": "PO accept",
                        "execution": "llm_task",
                        "action": "po_accept",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                        "on_success": "ACTIVE",
                        "side_effects": [
                            {"type": "transform_spec", "params": {"status": "completed"}, "optional": False},
                        ],
                    },
                    {
                        "key": "ACTIVE",
                        "label": "Active sprint",
                        "execution": "auto",
                        "action": "start_task",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                        "side_effects": [],
                    },
                    {
                        "key": "CLOSED",
                        "label": "Closed sprint",
                        "execution": "manual",
                        "action": "close_sprint",
                        "input_schema": {"type": "object"},
                        "output_schema": {
                            "type": "object",
                            "properties": {
                                "status": {"type": "string", "enum": ["closed"]},
                                "sprint_id": {"type": "string"},
                                "archived": {"type": "boolean"},
                            },
                        },
                        "side_effects": [
                            {"type": "emit_event", "params": {"event": "sprint_closed"}, "optional": False},
                            {"type": "archive_sprint", "params": {}, "optional": True},
                            {"type": "reindex_delta", "params": {}, "optional": False},
                        ],
                    },
                ]
            },
        },
        "edges": [],
        "on_events": {},
    }

    parent = os.path.dirname(os.path.dirname(chain_dir))
    wf_dir = os.path.join(parent, "workflow.v3")
    os.makedirs(wf_dir, exist_ok=True)
    with open(os.path.join(wf_dir, "workflow.json"), "w") as f:
        json.dump(workflow, f, indent=2)


def _setup_delta(chain_dir: str) -> None:
    """Crea el spring y spec iniciales en delta."""
    svc = spring_service(chain_dir=chain_dir)
    assert svc is not None
    spring_id = svc.get_spring_id(chain_dir)
    svc.create_spring(spring_id, goal="E2E workflow test", project_id="walker", session_id="sess-e2e")
    svc.add_spec(
        spring_id,
        objective="E2E test spec",
        domain="test",
        spec_type="feature",
        spec_index=0,
        status="draft",
    )


def _setup_workflow_state(chain_dir: str) -> None:
    """Crea el WorkflowState inicial."""
    state = WorkflowState(
        current_node="SPRINT",
        current_sub_state="PLANNING",
        sub_state_index=0,
        sub_state_data={},
        history=[],
        context={},
        version="5.0",
        updated_at="",
        node_executions=[],
        conflicts=[],
        blocked_at="",
        block_reason="",
        block_since="",
        pending_events=[],
        block_timeout_ms=0,
        pending_signals=[],
        signal_history=[],
        process_data={},
    )
    repo = WorkflowStateRepository()
    repo.save(chain_dir, state)





def _build_exec_ctx(chain_dir: str) -> "ExecutionContext":
    """Crea un ExecutionContext para el engine."""
    from shared.common.shared_domain.models import ExecutionContext
    return ExecutionContext.create(
        session_id="sess-e2e",
        chain_dir=chain_dir,
        task_id="SPEC-001",
        baggage={},
    )


# ── Tests E2E reales ─────────────────────────────────────────────────

class TestWorkflowE2EReal:
    """Flujo completo ejecutando el workflow engine real con mocks."""

    def test_full_workflow_executes_real_engine(self, tmp_path, monkeypatch):
        """Ejecuta el flujo completo usando OrchestrationService real.

        Composition root real (capa_rout.md D1): AppContainer.build() produce el
        registry poblado con las tasks reales (plan_sprint, spec_task,
        review_code, close_sprint se ejecutan REALES); register_mock_tasks()
        solo parchea las actions del flujo de test que no tienen handler real
        (gather_design, user_gate, implement, qa_run, ...).
        """
        from features.main.container import AppContainer

        project_root, chain_dir = _setup_project(tmp_path)

        app = AppContainer.build()
        registry = app.task_registry
        se = app.side_effect_engine
        register_mock_tasks(registry)
        parent = os.path.dirname(os.path.dirname(chain_dir))
        workflow_repo = WorkflowRepository(workflow_path=os.path.join(parent, "workflow.v3", "workflow.json"))
        state_repo = WorkflowStateRepository()
        svc = OrchestrationService(
            workflow_repo=workflow_repo,
            state_repo=state_repo,
            script_runner=ScriptRunner(),
            task_registry=registry,
            side_effect_engine=se,
        )

        # Estado inicial
        state = state_repo.load(chain_dir)
        assert state.current_node == "SPRINT"
        assert state.current_sub_state == "PLANNING"

        # ── Paso 1: SPRINT.PLANNING → auto-avance a TASK_SPEC ───────
        ctx1 = _build_exec_ctx(chain_dir)
        ctx1.baggage = {"node_key": "SPRINT.PLANNING"}
        result1 = svc.submit(ctx1, {"goal": "E2E test", "tasks": [{"objective": "Test", "type": "feature", "domain": "test"}]})
        assert "error" not in result1, result1.get("error")
        assert result1.get("advanced_to") == "TASK_SPEC"

        state = state_repo.load(chain_dir)
        assert state.current_sub_state == "TASK_SPEC"

        # ── Paso 2: SPRINT.TASK_SPEC → auto-avance a TASK_REVIEW ────
        ctx2 = _build_exec_ctx(chain_dir)
        ctx2.baggage = {"node_key": "SPRINT.TASK_SPEC"}
        result2 = svc.submit(ctx2, {
            "task_id": "SPEC-001",
            "status": "specified",
            "approval_type": "decision",
            "decision_data": {
                "decision": "Usar microservicios",
                "architecture": "API Gateway",
                "implementation": "3 servicios",
            },
        })
        assert "error" not in result2, result2.get("error")
        assert result2.get("advanced_to") == "TASK_REVIEW"

        state = state_repo.load(chain_dir)
        assert state.current_sub_state == "TASK_REVIEW"

        # Validar que la Spec pasó a "specified"
        svc_delta = spring_service(chain_dir=chain_dir)
        spring_id = svc_delta.get_spring_id(chain_dir)
        
        import features.delta_tracking as _dt_mod
        # monkeypatch restaura automáticamente al final del test (evita el
        # leak de estado global que rompía tests posteriores según el orden).
        monkeypatch.setattr(_dt_mod, "spring_service", lambda chain_dir="", **kw: svc_delta)
        
        spec = svc_delta.get_spec(spring_id, "SPEC-001")
        spec_dict = spec if isinstance(spec, dict) else spec.to_dict()
        assert spec_dict.get("status") == "specified"

        # ── Paso 3: SPRINT.TASK_REVIEW → approve → auto-avance a DESIGN_GATHER ─
        ctx3 = _build_exec_ctx(chain_dir)
        ctx3.baggage = {"node_key": "SPRINT.TASK_REVIEW"}
        result3 = svc.submit(ctx3, {
            "decision": "approve",
            "approval_type": "decision",
            "approval_data": {
                "decision": "Usar microservicios",
                "architecture": "API Gateway",
                "implementation": "3 servicios",
            },
        })
        assert "error" not in result3, result3.get("error")
        assert result3.get("advanced_to") == "DESIGN_GATHER"

        # Validar side effect: ADR creado
        adr_created = False
        for se_result in result3.get("side_effects", []):
            if se_result.get("type") == "create_adr":
                metadata = se_result.get("result", {}).get("metadata", {})
                if metadata.get("adr_id"):
                    adr_created = True
                    break
        assert adr_created, "create_adr no ejecutó correctamente"

        # Validar Spec approved
        spec = svc_delta.get_spec(spring_id, "SPEC-001")
        spec_dict = spec if isinstance(spec, dict) else spec.to_dict()
        assert spec_dict.get("status") == "approved"

        state = state_repo.load(chain_dir)
        assert state.current_node == "SPRINT"
        assert state.current_sub_state == "DESIGN_GATHER"

        # ── Paso 4: DESIGN_GATHER → ANALYZE_GATHER (submit + auto-avance) ─────
        ctx4 = _build_exec_ctx(chain_dir)
        ctx4.baggage = {"node_key": "SPRINT.DESIGN_GATHER"}
        result4 = svc.submit(ctx4, {"design_done": True})
        assert "error" not in result4, result4.get("error")
        assert result4.get("advanced_to") == "ANALYZE_GATHER"

        state = state_repo.load(chain_dir)
        assert state.current_node == "SPRINT"
        assert state.current_sub_state == "ANALYZE_GATHER"

        # ── Paso 5: ANALYZE_GATHER → IMPLEMENT_USER_GATE (submit + auto-avance) ──
        ctx5 = _build_exec_ctx(chain_dir)
        ctx5.baggage = {"node_key": "SPRINT.ANALYZE_GATHER"}
        result5 = svc.submit(ctx5, {"analysis_done": True})
        assert "error" not in result5, result5.get("error")
        assert result5.get("advanced_to") == "IMPLEMENT_USER_GATE"

        state = state_repo.load(chain_dir)
        assert state.current_node == "SPRINT"
        assert state.current_sub_state == "IMPLEMENT_USER_GATE"

        # ── Paso 6: IMPLEMENT_USER_GATE → IMPLEMENT_WORK (submit) ────────
        ctx6 = _build_exec_ctx(chain_dir)
        ctx6.baggage = {"node_key": "SPRINT.IMPLEMENT_USER_GATE"}
        result6 = svc.submit(ctx6, {"approved": True})
        assert "error" not in result6, result6.get("error")
        assert result6.get("advanced_to") == "IMPLEMENT_WORK"

        state = state_repo.load(chain_dir)
        assert state.current_node == "SPRINT"
        assert state.current_sub_state == "IMPLEMENT_WORK"

        # ── Paso 7: IMPLEMENT_WORK → CODE_REVIEW_SCAN (submit) ────────
        ctx7 = _build_exec_ctx(chain_dir)
        ctx7.baggage = {"node_key": "SPRINT.IMPLEMENT_WORK"}
        result7 = svc.submit(ctx7, {"code_complete": True})
        assert "error" not in result7, result7.get("error")
        assert result7.get("advanced_to") == "CODE_REVIEW_SCAN"

        state = state_repo.load(chain_dir)
        assert state.current_node == "SPRINT"
        assert state.current_sub_state == "CODE_REVIEW_SCAN"

        # Validar Spec en in_progress (desde IMPLEMENT_WORK)
        spec = svc_delta.get_spec(spring_id, "SPEC-001")
        spec_dict = spec if isinstance(spec, dict) else spec.to_dict()
        assert spec_dict.get("status") == "in_progress"

        # ── Paso 8: CODE_REVIEW_SCAN → CODE_REVIEW_RESULT (auto) ──────
        ctx8 = _build_exec_ctx(chain_dir)
        ctx8.baggage = {"node_key": "SPRINT.CODE_REVIEW_SCAN"}
        result8 = svc.submit(ctx8, {})
        assert "error" not in result8, result8.get("error")
        assert result8.get("advanced_to") == "CODE_REVIEW_RESULT"

        state = state_repo.load(chain_dir)
        assert state.current_node == "SPRINT"
        assert state.current_sub_state == "CODE_REVIEW_RESULT"

        # ── Paso 9: CODE_REVIEW_RESULT → REPORT_GATE (submit) ─────────
        ctx9 = _build_exec_ctx(chain_dir)
        ctx9.baggage = {"node_key": "SPRINT.CODE_REVIEW_RESULT"}
        result9 = svc.submit(ctx9, {"review_passed": True})
        assert "error" not in result9, result9.get("error")
        assert result9.get("advanced_to") == "REPORT_GATE"

        state = state_repo.load(chain_dir)
        assert state.current_node == "SPRINT"
        assert state.current_sub_state == "REPORT_GATE"

        # ── Paso 10: REPORT_GATE → VERIFY_USER_GATE (submit + auto-avance) ───────
        ctx10 = _build_exec_ctx(chain_dir)
        ctx10.baggage = {"node_key": "SPRINT.REPORT_GATE"}
        result10 = svc.submit(ctx10, {"report_approved": True})
        assert "error" not in result10, result10.get("error")
        assert result10.get("advanced_to") == "VERIFY_USER_GATE"

        state = state_repo.load(chain_dir)
        assert state.current_node == "SPRINT"
        assert state.current_sub_state == "VERIFY_USER_GATE"

        # Validar Spec completed
        spec = svc_delta.get_spec(spring_id, "SPEC-001")
        spec_dict = spec if isinstance(spec, dict) else spec.to_dict()
        assert spec_dict.get("status") == "completed"

        # ── Paso 11: VERIFY_USER_GATE → QA_RUN (submit) ───────────────
        ctx11 = _build_exec_ctx(chain_dir)
        ctx11.baggage = {"node_key": "SPRINT.VERIFY_USER_GATE"}
        result11 = svc.submit(ctx11, {"approved": True})
        assert "error" not in result11, result11.get("error")
        assert result11.get("advanced_to") == "QA_RUN"

        state = state_repo.load(chain_dir)
        assert state.current_node == "SPRINT"
        assert state.current_sub_state == "QA_RUN"

        # ── Paso 12: QA_RUN → PO_ACCEPT (submit) ───────────────
        ctx12 = _build_exec_ctx(chain_dir)
        ctx12.baggage = {"node_key": "SPRINT.QA_RUN"}
        result12 = svc.submit(ctx12, {"qa_passed": True})
        assert "error" not in result12, result12.get("error")
        assert result12.get("advanced_to") == "PO_ACCEPT"

        state = state_repo.load(chain_dir)
        assert state.current_node == "SPRINT"
        assert state.current_sub_state == "PO_ACCEPT"

        # ── Paso 13: PO_ACCEPT → ACTIVE (submit + auto-avance) ──
        ctx13 = _build_exec_ctx(chain_dir)
        ctx13.baggage = {"node_key": "SPRINT.PO_ACCEPT"}
        result13 = svc.submit(ctx13, {"po_approved": True})
        assert "error" not in result13, result13.get("error")
        assert result13.get("advanced_to") == "ACTIVE"

        state = state_repo.load(chain_dir)
        assert state.current_node == "SPRINT"
        assert state.current_sub_state == "ACTIVE"
