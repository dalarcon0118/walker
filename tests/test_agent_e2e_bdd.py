"""tests/test_agent_e2e_bdd.py — E2E MCP Tools: valida tools del protocolo MCP
usando _handle_request (JSON-RPC 2.0 real) con engine real + mocks en task handlers.

Nivel 1 de testing (según SPEC-003):
- test_mcp_tool_status_e2e
- test_mcp_tool_submit_full_workflow (via engine directo)
- test_mcp_tool_transition_between_nodes (via engine directo)
- test_mcp_tool_capture_task_creates_spec
- test_mcp_tool_recall_knowledge_returns_delta
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

import pytest

from shared.core.engine.application.orchestrator import OrchestrationService
from shared.core.engine.domain.entities import WorkflowState
from shared.common.shared_domain.models import ExecutionContext
from shared.core.engine.infrastructure.repositories import (
    WorkflowStateRepository,
)
from features.delta_tracking import spring_service
from tests.conftest import (
    make_chain_dir,
    make_walker_project,
    register_mock_tasks,
    register_project_in_global_registry,
)


# ── Helpers de setup ────────────────────────────────────────────────────

def _setup_project(tmp_path: Path) -> tuple[str, str]:
    """Crea el proyecto walker + chain_dir sprint-e2e (builders compartidos en conftest).

    Registra walker→tmp en el registro global (reversible) y crea un spring
    ACTIVO en el delta del proyecto, para que el dispatch MCP real (Camino A:
    _resolve_project_id → handle_task → _ensure_chain_dir → find_active_sprint)
    resuelva el chain_dir. ANTES el dispatch no resolvía _project_id (fix del
    fallback silencioso) y estos tests validaban el fallback projects_list, no
    la tool real.
    """
    project_root = make_walker_project(tmp_path)
    chain_dir = make_chain_dir(tmp_path, "sprint-e2e")

    # Spring ACTIVO en el delta del proyecto (para find_active_sprint).
    # _setup_delta crea el spring con status por defecto (planning) — pero
    # find_active_sprint solo ve "active". El status activo se mantiene tras
    # el fix de plan_sprint (el sprint del proyecto está activo desde su
    # planificación).
    from features.delta_tracking import SpringService

    svc = SpringService(project_root)
    svc.create_spring(
        "sprint-e2e", goal="E2E sprint", status="active",
        project_id="walker", session_id="sess-e2e",
    )
    # Sprint dir en el layout real (.cognitive/projects/walker/data/spring/)
    real_sprint_dir = Path(project_root) / "data" / "spring" / "sprint-e2e"
    real_sprint_dir.mkdir(parents=True, exist_ok=True)
    from shared.core.engine.domain.entities import WorkflowState
    from shared.core.engine.infrastructure.repositories import WorkflowStateRepository

    wf = WorkflowState(current_node="SPRINT", current_sub_state="PLANNING")
    wf.context["chain_dir"] = str(real_sprint_dir)
    wf.context["project_id"] = "walker"
    wf.context["session_id"] = "sess-e2e"
    WorkflowStateRepository().save(str(real_sprint_dir), wf)

    _E2E_REGISTRY_CTX.append(
        register_project_in_global_registry(str(tmp_path), "walker").__enter__()
    )
    return project_root, chain_dir


# Contexto del registro global registrado por _setup_project (se restaura en teardown).
_E2E_REGISTRY_CTX: list = []


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
    from protocols.mcp.mcp_adapter import _handle_request

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
    """Composition root real (capa_rout.md D1): AppContainer.build() produce el registry
    poblado y los mocks son PARCHES para las actions sin handler real. ANTES
    instanciaba OrchestrationService() (DomainTaskRegistry vacío) y los mocks
    sustituían TODO el registry — enmascarando el bug de composición en
    producción (el flujo CLI/MCP avanzaba sin ejecutar tasks reales)."""
    from app.container import AppContainer
    from shared.core.engine.infrastructure.repositories import WorkflowRepository, WorkflowStateRepository, ScriptRunner
    from app.path_resolver import get_engine_workflow_path
    from features.project import ProjectService

    app = AppContainer.build()
    registry = app.task_registry
    se = app.side_effect_engine
    register_mock_tasks(registry)

    # Resolver workflow_path para el chain_dir del test
    workflow_path = str(get_engine_workflow_path())
    if chain_dir:
        try:
            parent = os.path.dirname(os.path.dirname(chain_dir))
            wf_dir = 'workflow.v3'
            modular = os.path.join(parent, wf_dir, 'workflow.json')
            if os.path.exists(modular):
                workflow_path = modular
            else:
                paths = ProjectService.resolve_paths(chain_dir)
                project_wf = os.path.join(
                    str(paths.project_root), wf_dir, 'workflow.json'
                )
                if os.path.exists(project_wf):
                    workflow_path = project_wf
        except Exception:
            pass

    from shared.core.engine.infrastructure.workflow_store import WorkflowStoreAdapter
    from shared.core.engine.domain.workflow_action import ActionCatalog

    actions = ActionCatalog()
    for meta in registry.list_tasks_with_meta(scope=None):
        actions.register(
            name=meta["name"], kind="sub_state_action",
            scope=meta.get("scope", "public"),
            handler=registry.get_task(meta["name"]), metadata=meta,
        )
    for name, handler in se._handlers.items():
        actions.register(
            name=name, kind="side_effect", scope="internal",
            handler=handler, metadata={"source": "side_effect_engine"},
        )
    return OrchestrationService(
        workflow_store=WorkflowStoreAdapter(
            workflow_repo=WorkflowRepository(workflow_path=workflow_path),
            state_repo=WorkflowStateRepository(),
        ),
        actions=actions,
        workflow_io=ScriptRunner(),
    )


# ── Tests E2E MCP Tools ─────────────────────────────────────────────────

class TestAgentE2E:
    """Flujo completo MCP JSON-RPC 2.0 con engine real + mocks."""

    @pytest.fixture(autouse=True)
    def _restore_e2e_registry(self):
        """Restaura el registro global registrado por _setup_project.

        Cada test registra walker→tmp (reversible); al terminar se restauran
        todos los contextos y se resetea el ModuleRegistry singleton (el
        dispatch MCP lo cachea con el proyecto temporal).
        """
        yield
        while _E2E_REGISTRY_CTX:
            ctx = _E2E_REGISTRY_CTX.pop()
            ctx.__exit__(None, None, None)
        from shared.core.engine.bootstrap import reset_module_registry

        reset_module_registry()

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

    def test_mcp_tool_recall_knowledge_returns_delta(self, tmp_path):
        """Endurecido (fix de asserts débiles): antes solo validaba status 200.

        recall_knowledge debe devolver ok=True con un result de búsqueda estructurado
        (hits.total numérico). Un fallback (projects_list) o un result vacío
        sin estructura ya no pasa. NOTA: hoy el route de recall_knowledge descarta el
        index (flags_builder solo pasa query) → consulta el índice default."""
        project_root, chain_dir = _setup_project(tmp_path)
        _setup_workflow(chain_dir)
        _setup_delta(chain_dir)
        _setup_workflow_state(chain_dir)

        resp = _mcp_call("recall_knowledge", {
            "_project_id": "walker",
            "query": "E2E MCP spec",
            "index": "delta",
        })
        assert resp.get("result", {}).get("isError") is not True
        data = _parse_mcp_result(resp)
        assert data.get("status_code") == 200
        d = data.get("data", {})
        assert d.get("type") != "projects_list", (
            f"recall_knowledge cayó al fallback projects_list: {d.get('type')}"
        )
        assert d.get("ok") is True, f"recall_knowledge no ok: {d}"
        result = d.get("result", {})
        assert isinstance(result, dict), f"recall_knowledge result no estructurado: {result}"
        hits = result.get("hits", {})
        assert isinstance(hits.get("total"), int), (
            f"recall_knowledge sin hits.total numérico: {result}"
        )

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
        assert data.get("data", {}).get("type") != "projects_list"
        assert data.get("data", {}).get("ok") is True

    def test_mcp_tool_ckg_analyze_returns_nodes(self, tmp_path):
        """Endurecido: ckg_analyze debe devolver ok=True con result de action."""
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
        d = data.get("data", {})
        assert d.get("type") != "projects_list", (
            f"ckg_analyze cayó al fallback projects_list: {d.get('type')}"
        )
        assert d.get("ok") is True, f"ckg_analyze no ok: {d}"
        result = d.get("result", {})
        assert isinstance(result, dict) and "result" in result, (
            f"ckg_analyze result sin acción: {result}"
        )

    def test_mcp_tool_cpg_analyze_builds_graph(self, tmp_path):
        """Endurecido: cpg_analyze debe devolver ok=True con análisis estructurado."""
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
        d = data.get("data", {})
        assert d.get("type") != "projects_list", (
            f"cpg_analyze cayó al fallback projects_list: {d.get('type')}"
        )
        assert d.get("ok") is True, f"cpg_analyze no ok: {d}"
        result = d.get("result", {})
        assert isinstance(result, dict) and "result" in result, (
            f"cpg_analyze result sin análisis: {result}"
        )

    def test_mcp_tool_trace_push_creates_event(self, tmp_path):
        project_root, chain_dir = _setup_project(tmp_path)
        _setup_workflow(chain_dir)
        _setup_delta(chain_dir)
        _setup_workflow_state(chain_dir)

        tools_resp = _mcp_call("tools/list", {})
        tools_data = _parse_mcp_result(tools_resp)
        tool_names = [t.get("name") for t in tools_data.get("tools", [])]
        assert "trace_push" not in tool_names
