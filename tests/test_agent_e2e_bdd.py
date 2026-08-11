"""tests/test_agent_e2e_bdd.py — E2E MCP Tools: valida tools del protocolo MCP
usando _handle_request (JSON-RPC 2.0 real) con engine real + mocks en task handlers.

Nivel 1 de testing (según SPEC-003):
- test_mcp_tool_status_e2e
- test_mcp_tool_submit_full_workflow (via engine directo)
- test_mcp_tool_transition_between_nodes (via engine directo)
- test_mcp_tool_capture_task_creates_spec
- test_mcp_tool_kg_search_returns_delta
- test_mcp_tool_save_to_ckg_persists_decision
- test_mcp_tool_ckg_analyze_returns_nodes
- test_mcp_tool_cpg_analyze_builds_graph
- test_mcp_tool_trace_push_creates_event
- test_mcp_tool_help_lists_all_tools
- test_mcp_tool_list_projects_shows_walker
- test_mcp_tool_prepare_creates_project
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from features.engine.application.orchestrator import OrchestrationService
from features.engine.domain.entities import WorkflowState
from libs.shared_domain.models import ExecutionContext
from features.engine.infrastructure.repositories import (
    WorkflowStateRepository,
)
from features.delta_tracking import spring_service
from tests.conftest import (
    make_chain_dir,
    make_walker_project,
    register_mock_tasks,
    register_side_effects,
)


# ── Helpers de setup ────────────────────────────────────────────────────

def _setup_project(tmp_path: Path) -> tuple[str, str]:
    """Crea el proyecto walker + chain_dir sprint-e2e (builders compartidos en conftest)."""
    return make_walker_project(tmp_path), make_chain_dir(tmp_path, "sprint-e2e")


def _setup_workflow(chain_dir: str) -> None:
    workflow = {
        "label": "E2E Workflow",
        "description": "Flujo completo de prueba E2E",
        "initial_state": "SPRINT_PLANNING",
        "nodes": {
            "SPRINT_PLANNING": {
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
                                "type": "create_adr_from_spec",
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


def _add_gate_edge(chain_dir: str, gate: str = "plan_approved") -> None:
    """Añade un nodo destino real + edge con gate al workflow de prueba.

    El test de transition necesita una transición ENTRE nodos (SPRINT_PLANNING
    → ACTIVE_NODE), no un self-loop. Se registra el nodo ACTIVE_NODE con su
    sub-estado inicial y la edge gate que lo alcanza.
    """
    parent = os.path.dirname(os.path.dirname(chain_dir))
    wf_path = os.path.join(parent, "workflow.v3", "workflow.json")
    with open(wf_path) as f:
        workflow = json.load(f)
    workflow.setdefault("nodes", {})["ACTIVE_NODE"] = {
        "label": "Active sprint (nodo)",
        "sub_states": [
            {
                "key": "START",
                "execution": "manual",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "side_effects": [],
            }
        ],
    }
    workflow.setdefault("edges", []).append(
        {
            "from": "SPRINT_PLANNING",
            "to": "ACTIVE_NODE",
            "gate": gate,
            "description": "plan approved",
        }
    )
    with open(wf_path, "w") as f:
        json.dump(workflow, f, indent=2)


def _setup_delta(chain_dir: str) -> None:
    svc = spring_service(chain_dir=chain_dir)
    assert svc is not None
    spring_id = svc.get_spring_id(chain_dir)
    svc.create_spring(spring_id, goal="E2E MCP test", project_id="walker", session_id="sess-e2e")
    svc.add_spec(
        spring_id,
        objective="E2E MCP spec",
        domain="test",
        spec_type="feature",
        spec_index=0,
        status="draft",
    )


def _setup_workflow_state(chain_dir: str) -> None:
    state = WorkflowState(
        current_node="SPRINT_PLANNING",
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





def _mcp_call(name: str, arguments: dict) -> dict:
    from features.cli_interface.mcp_adapter import _handle_request

    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    return _handle_request(msg)


def _parse_mcp_result(resp: dict) -> dict:
    content = resp.get("result", {}).get("content", [])
    text = content[0].get("text", "") if content else ""
    return json.loads(text) if text.startswith("{") else {}


def _get_orchestration_service(chain_dir: str) -> OrchestrationService:
    svc = OrchestrationService()
    register_side_effects(svc._side_effect_engine)
    register_mock_tasks(svc._task_registry)
    return svc


# ── Tests E2E MCP Tools ─────────────────────────────────────────────────

class TestAgentE2E:
    """Flujo completo MCP JSON-RPC 2.0 con engine real + mocks."""

    def test_mcp_tool_status_e2e(self, tmp_path):
        project_root, chain_dir = _setup_project(tmp_path)
        _setup_workflow(chain_dir)
        _setup_delta(chain_dir)
        _setup_workflow_state(chain_dir)

        resp = _mcp_call("status", {"project_id": "walker"})
        assert resp.get("result", {}).get("isError") is not True
        data = _parse_mcp_result(resp)
        assert data.get("status_code") == 200

    def test_mcp_tool_help_lists_all_tools(self, tmp_path):
        resp = _mcp_call("help", {})
        assert resp.get("result", {}).get("isError") is not True
        data = _parse_mcp_result(resp)
        assert data.get("status_code") == 200
        assert data.get("data", {}).get("type") == "help"

    def test_mcp_tool_list_projects_shows_walker(self, tmp_path):
        project_root, chain_dir = _setup_project(tmp_path)
        resp = _mcp_call("list", {})
        assert resp.get("result", {}).get("isError") is not True
        data = _parse_mcp_result(resp)
        assert data.get("status_code") == 200
        projects = data.get("data", {}).get("projects", [])
        assert any(p.get("project_id") == "walker" for p in projects)

    def test_mcp_tool_prepare_creates_project(self, tmp_path):
        target = str(tmp_path / "newproj")
        os.makedirs(target, exist_ok=True)
        resp = _mcp_call("prepare", {"path": target})
        assert resp.get("result", {}).get("isError") is not True
        data = _parse_mcp_result(resp)
        assert data.get("status_code") == 200
        assert os.path.exists(os.path.join(target, ".cognitive", "projects", "newproj", "data", "project.json"))

    def test_mcp_tool_capture_task_creates_spec(self, tmp_path):
        project_root, chain_dir = _setup_project(tmp_path)
        _setup_workflow(chain_dir)
        _setup_delta(chain_dir)
        _setup_workflow_state(chain_dir)

        resp = _mcp_call("capture_task", {
            "_project_id": "walker",
            "objective": "E2E capture task",
            "domain": "test",
            "files": ["test.py"],
        })
        assert resp.get("result", {}).get("isError") is not True
        data = _parse_mcp_result(resp)
        assert data.get("status_code") == 200

    def test_mcp_tool_submit_full_workflow(self, tmp_path):
        project_root, chain_dir = _setup_project(tmp_path)
        _setup_workflow(chain_dir)
        _setup_delta(chain_dir)
        _setup_workflow_state(chain_dir)

        svc = _get_orchestration_service(chain_dir)
        ctx = ExecutionContext.create(
            session_id="sess-e2e",
            chain_dir=chain_dir,
            task_id="SPEC-001",
            baggage={},
        )
        result = svc.submit(ctx, {
            "goal": "E2E MCP test",
            "tasks": [{"objective": "Test", "type": "feature", "domain": "test"}],
        })
        assert "error" not in result, result.get("error")
        assert result.get("advanced_to") == "TASK_SPEC"

        state_repo = WorkflowStateRepository()
        state = state_repo.load(chain_dir)
        assert state.current_sub_state == "TASK_SPEC"

    def test_mcp_tool_transition_between_nodes(self, tmp_path):
        project_root, chain_dir = _setup_project(tmp_path)
        _setup_workflow(chain_dir)
        _add_gate_edge(chain_dir)
        _setup_delta(chain_dir)
        _setup_workflow_state(chain_dir)

        svc = _get_orchestration_service(chain_dir)
        ctx = ExecutionContext.create(
            session_id="sess-e2e",
            chain_dir=chain_dir,
            task_id="SPEC-001",
            baggage={},
        )
        result = svc.transition(ctx, "plan_approved", skip_preconditions=True)
        assert "error" not in result, result.get("error")
        assert result.get("ok") is True

        state_repo = WorkflowStateRepository()
        state = state_repo.load(chain_dir)
        assert state.current_node == "ACTIVE_NODE"
        assert state.current_sub_state == "START"

    def test_mcp_tool_kg_search_returns_delta(self, tmp_path):
        project_root, chain_dir = _setup_project(tmp_path)
        _setup_workflow(chain_dir)
        _setup_delta(chain_dir)
        _setup_workflow_state(chain_dir)

        resp = _mcp_call("kg_search", {
            "query": "E2E MCP spec",
            "index": "delta",
        })
        assert resp.get("result", {}).get("isError") is not True
        data = _parse_mcp_result(resp)
        assert data.get("status_code") == 200

    def test_mcp_tool_save_to_ckg_persists_decision(self, tmp_path):
        project_root, chain_dir = _setup_project(tmp_path)
        _setup_workflow(chain_dir)
        _setup_delta(chain_dir)
        _setup_workflow_state(chain_dir)

        resp = _mcp_call("save_to_ckg", {
            "_project_id": "walker",
            "statement": "E2E: la decision debe persistir en CKG",
            "tags": ["e2e"],
            "type": "decision",
        })
        assert resp.get("result", {}).get("isError") is not True
        data = _parse_mcp_result(resp)
        assert data.get("status_code") == 200
        assert data.get("data", {}).get("ok") is True

    def test_mcp_tool_ckg_analyze_returns_nodes(self, tmp_path):
        project_root, chain_dir = _setup_project(tmp_path)
        _setup_workflow(chain_dir)
        _setup_delta(chain_dir)
        _setup_workflow_state(chain_dir)

        resp = _mcp_call("ckg_analyze", {
            "_project_id": "walker",
            "action": "search",
            "query": "E2E",
        })
        assert resp.get("result", {}).get("isError") is not True
        data = _parse_mcp_result(resp)
        assert data.get("status_code") == 200

    def test_mcp_tool_cpg_analyze_builds_graph(self, tmp_path):
        project_root, chain_dir = _setup_project(tmp_path)
        _setup_workflow(chain_dir)
        _setup_delta(chain_dir)
        _setup_workflow_state(chain_dir)

        resp = _mcp_call("cpg_analyze", {
            "_project_id": "walker",
            "action": "health",
        })
        assert resp.get("result", {}).get("isError") is not True
        data = _parse_mcp_result(resp)
        assert data.get("status_code") == 200

    def test_mcp_tool_trace_push_creates_event(self, tmp_path):
        project_root, chain_dir = _setup_project(tmp_path)
        _setup_workflow(chain_dir)
        _setup_delta(chain_dir)
        _setup_workflow_state(chain_dir)

        resp = _mcp_call("trace_push", {
            "_project_id": "walker",
            "event_type": "test_event",
            "data": {"test": True},
        })
        assert resp.get("result", {}).get("isError") is not True
        data = _parse_mcp_result(resp)
        assert data.get("ok") is True
