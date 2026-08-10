"""Tests BDD (pytest-bdd) — MCP Workflow Tools: submit, transition, capture_task.

Flujo completo JSON-RPC: tools/call -> REST handler -> use case.

Escenarios: tests/features/mcp_workflow_tools.feature
"""
from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/mcp_workflow_tools.feature")


@pytest.fixture
def mcp_wf_ctx() -> dict:
    return {}


@given("un proyecto MCP con .cognitive/", target_fixture="mcp_wf_ctx")
def _mcp_project(mcp_wf_ctx: dict, mcp_project):
    mcp_wf_ctx["project_root"] = mcp_project
    return mcp_wf_ctx


@given('invoco la tool MCP submit con decision test y evidence ok', target_fixture="mcp_wf_ctx")
def _call_submit(mcp_wf_ctx: dict):
    from tests.conftest import call_mcp_tool
    mcp_wf_ctx["resp"] = call_mcp_tool(mcp_wf_ctx["project_root"], "submit", {"decision": "test", "evidence": "ok"})
    return mcp_wf_ctx


@given('invoco la tool MCP transition con gate start_task', target_fixture="mcp_wf_ctx")
def _call_transition(mcp_wf_ctx: dict):
    from tests.conftest import call_mcp_tool
    mcp_wf_ctx["resp"] = call_mcp_tool(mcp_wf_ctx["project_root"], "transition", {"gate": "start_task"})
    return mcp_wf_ctx


@given('invoco la tool MCP capture_task con objective Trabajo informal y files test.py', target_fixture="mcp_wf_ctx")
def _call_capture(mcp_wf_ctx: dict):
    from tests.conftest import call_mcp_tool
    mcp_wf_ctx["resp"] = call_mcp_tool(mcp_wf_ctx["project_root"], "capture_task", {"objective": "Trabajo informal", "domain": "test", "files": ["test.py"]})
    return mcp_wf_ctx


@given('invoco la tool MCP submit vacía', target_fixture="mcp_wf_ctx")
def _call_submit_empty(mcp_wf_ctx: dict):
    from tests.conftest import call_mcp_tool
    mcp_wf_ctx["resp"] = call_mcp_tool(mcp_wf_ctx["project_root"], "submit", {})
    return mcp_wf_ctx


@given('invoco la tool MCP transition con gate no_existe', target_fixture="mcp_wf_ctx")
def _call_transition_invalid(mcp_wf_ctx: dict):
    from tests.conftest import call_mcp_tool
    mcp_wf_ctx["resp"] = call_mcp_tool(mcp_wf_ctx["project_root"], "transition", {"gate": "no_existe"})
    return mcp_wf_ctx


@then("la respuesta MCP es exitosa")
def _assert_mcp_ok(mcp_wf_ctx: dict):
    resp = mcp_wf_ctx.get("resp", {})
    assert resp.get("type") != "error", f"expected ok, got: {resp}"


@then("el resultado tiene type ok")
def _assert_type_ok(mcp_wf_ctx: dict):
    assert mcp_wf_ctx.get("resp", {}).get("type") == "ok"


@then("la respuesta MCP indica error")
def _assert_mcp_error(mcp_wf_ctx: dict):
    resp = mcp_wf_ctx.get("resp", {})
    assert resp.get("type") == "error" or resp.get("status_code", 200) >= 400, f"expected error, got: {resp}"
