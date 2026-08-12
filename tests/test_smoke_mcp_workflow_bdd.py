"""tests/test_smoke_mcp_workflow_bdd.py — Smoke funcional Capa 1.

Recorre el WORKFLOW REAL (features/engine/workflow/ — nodo SPRINT) a través
del protocolo MCP (tools submit vía _handle_request JSON-RPC 2.0), con tasks
REALES (plan_sprint → PlanSprintUseCase). Asserts por paso sobre:
  - WorkflowState evoluciona (node / sub_state)
  - DeltaGraph tiene el spring/spec creados por las tasks reales
  - TraceGraph tiene los eventos (task_execute / SUBMIT)

Es el smoke de CI que valida MCP + side effects + memorias sin LLM.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from features.delta_tracking import SpringService
from features.engine.domain.entities import WorkflowState
from features.engine.infrastructure.repositories import WorkflowStateRepository
from tests.conftest import register_project_in_global_registry

SPRINT_GOAL = "Implementar modulo de autenticacion completo"


@pytest.fixture
def _smoke_project(tmp_path):
    """Registra el proyecto (reversible) y monta el proyecto cognitivo.

    Restaura el registro global al final del test (no contamina entre
    corridas) — el registro se aísla igual que _sandbox_global_registry.
    También resetea el ModuleRegistry singleton (el smoke dispara
    bootstrap_modules() que cachea el proyecto temporal; sin reset, los
    tests MCP posteriores resolverían el proyecto equivocado).
    """
    tmp = str(tmp_path)
    reg_ctx = register_project_in_global_registry(tmp).__enter__()
    try:
        yield _setup_project(tmp)
    finally:
        reg_ctx.__exit__(None, None, None)
        from features.engine.bootstrap import reset_module_registry

        reset_module_registry()
        # El McpServer es otro singleton que cachea los handlers de los
        # módulos; se invalida junto al ModuleRegistry para que el próximo
        # test reconstruya el dispatch con el registro global restaurado.
        from features.cli_interface import mcp_adapter

        mcp_adapter._SERVER = None


def _setup_project(tmp_path: str) -> str:
    """Crea el proyecto cognitivo + spring activo + workflow state inicial.

    Layout real del walker (no el chain_dir plano de los tests legacy):
      .cognitive/projects/walker/data/spring/sprint-smoke/
    """
    cog = Path(tmp_path) / ".cognitive" / "projects" / "walker"
    cog.mkdir(parents=True)
    (cog / "data").mkdir()
    (cog / "data" / "project.json").write_text(json.dumps({
        "project_id": "walker",
        "name": "walker",
        "repo_path": tmp_path,
        "language": "python",
        "framework": "unknown",
        "status": "active",
    }))
    from features.delta_tracking.infrastructure.cognitive_delta_repo import (
        CognitiveDeltaRepository,
    )

    CognitiveDeltaRepository(str(cog))

    svc = SpringService(str(cog))
    svc.create_spring(
        "sprint-smoke", goal="Smoke sprint", status="active",
        project_id="walker", session_id="s1",
    )

    sprint_dir = cog / "data" / "spring" / "sprint-smoke"
    sprint_dir.mkdir(parents=True, exist_ok=True)

    wf = WorkflowState(current_node="SPRINT", current_sub_state="PLANNING")
    wf.context["chain_dir"] = str(sprint_dir)
    wf.context["project_id"] = "walker"
    wf.context["session_id"] = "s1"
    WorkflowStateRepository().save(str(sprint_dir), wf)
    return str(sprint_dir)


def _mcp_call(name: str, arguments: dict) -> dict:
    """Invoca una tool MCP vía _handle_request (JSON-RPC 2.0 real)."""
    from features.cli_interface.mcp_adapter import _handle_request

    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    return _handle_request(msg)


def _parse(resp: dict) -> dict:
    content = resp.get("result", {}).get("content", [])
    text = content[0].get("text", "") if content else ""
    return json.loads(text) if text.startswith("{") else {}


def _assert_mcp_ok(resp: dict, tool: str) -> dict:
    """Assert fuerte (fix de asserts débiles): valida type + ok + estructura."""
    data = _parse(resp)
    assert data.get("status_code") == 200, f"{tool} status != 200: {data}"
    d = data.get("data", {})
    assert d.get("type") != "projects_list", (
        f"{tool} cayó al fallback projects_list: {d.get('type')}"
    )
    return d


class TestSmokeMCPWorkflow:
    """Capa 1: flujo real del nodo SPRINT recorrido vía MCP submit."""

    def test_smoke_plan_sprint_via_mcp_ejecuta_task_real(self, tmp_path, _smoke_project):
        """PLANNING: submit vía MCP ejecuta plan_sprint REAL y crea la spec en delta."""
        sprint_dir = _smoke_project

        resp = _mcp_call("submit", {"project_id": "walker", "body": {
            "goal": SPRINT_GOAL,
            "tasks": [
                {"objective": "Login screen", "type": "feature", "domain": "frontend"},
            ],
        }})
        d = _assert_mcp_ok(resp, "submit(PLANNING)")
        # La task REAL se ejecutó (fix del DI) — no un submit congelado
        assert d.get("task_executed") is True, f"plan_sprint no se ejecutó: {d}"
        assert d.get("task_action") == "plan_sprint", d

        # 1. WorkflowState avanzó: PLANNING → PLAN_APPROVAL
        state = WorkflowStateRepository().load(sprint_dir)
        assert state.current_node == "SPRINT", state.current_node
        assert state.current_sub_state == "PLAN_APPROVAL", state.current_sub_state

        # 2. DeltaGraph: spring activo + spec creada por PlanSprintUseCase real
        svc = SpringService(str(Path(sprint_dir).parent.parent.parent))
        specs = svc.get_specs("sprint-smoke")
        assert specs, "plan_sprint real no creó specs en el DeltaGraph"
        assert len(specs) >= 1, f"specs esperadas >= 1, got {len(specs)}"

    def test_smoke_plan_approval_via_mcp(self, tmp_path, _smoke_project):
        """PLAN_APPROVAL: submit decision=approve + transition plan_approval_approved.

        PLAN_APPROVAL es user_input con decisión binaria: el submit persiste la
        decisión y la gate XOR (plan_approval_approved) la aplica vía transition
        → TASK_SPEC (edges.json). Un submit solo NO auto-avanza user_input."""
        sprint_dir = _smoke_project
        # Precondición: PLANNING → PLAN_APPROVAL (submit anterior)
        _mcp_call("submit", {"project_id": "walker", "body": {
            "goal": SPRINT_GOAL,
            "tasks": [{"objective": "Login screen", "type": "feature", "domain": "frontend"}],
        }})
        state = WorkflowStateRepository().load(sprint_dir)
        assert state.current_sub_state == "PLAN_APPROVAL"

        # 1. Submit de la decisión (persiste el intent y avanza el workflow).
        # El agente real lee la spec creada por plan_sprint y refiere su
        # task_id — la decisión approve con task_id resuelve el XOR y el
        # OrchestrationService completo avanza a TASK_SPEC.
        svc = SpringService(str(Path(sprint_dir).parent.parent.parent))
        specs = svc.get_specs("sprint-smoke")
        task_id = specs[0]["spec_id"] if specs else "TASK-001"
        resp = _mcp_call("submit", {"project_id": "walker", "body": {
            "decision": "approve",
            "task_id": task_id,
        }})
        d = _assert_mcp_ok(resp, "submit(PLAN_APPROVAL)")
        assert d.get("submitted") is True, f"decisión no persistida: {d}"

        state = WorkflowStateRepository().load(sprint_dir)
        assert state.current_sub_state == "TASK_SPEC", state.current_sub_state

    def test_smoke_trace_tiene_eventos_del_flujo(self, tmp_path, _smoke_project):
        """TraceGraph: el flujo MCP registra eventos (task_execute / SUBMIT)."""
        sprint_dir = _smoke_project

        _mcp_call("submit", {"project_id": "walker", "body": {
            "goal": SPRINT_GOAL,
            "tasks": [{"objective": "Login screen", "type": "feature", "domain": "frontend"}],
        }})

        from features.event_tracing import search

        events = search(sprint_dir, {})
        assert events, "TraceGraph vacío tras el flujo MCP"
        types = {e.get("event_type") for e in events}
        print(f"\nTRACE event_types: {sorted(types)}")
        # El task_execute de plan_sprint (o el SUBMIT) debe estar presente
        assert any(
            e.get("event_type") == "task_execute"
            or (e.get("data") or {}).get("sub_state") == "PLANNING"
            for e in events
        ), f"sin evento del submit en trace: {sorted(types)}"
