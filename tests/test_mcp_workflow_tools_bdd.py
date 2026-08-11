"""Tests BDD (pytest-bdd) — MCP Workflow Tools: submit, transition, capture_task.

Flujo completo JSON-RPC: tools/call -> REST handler -> use case.

Nota: submit y transition tienen recursion MCP en sus handlers (usan
mcp.call_tool internamente), por lo que solo se testean casos de error
via MCP. El flujo exitoso se cubre en test_workflow_spring_bdd.py
via handler directo.

Escenarios: bdd/features/mcp_workflow_tools.feature
"""
from __future__ import annotations

import json
import os

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/mcp_workflow_tools.feature")


@pytest.fixture
def mcp_wf_ctx() -> dict:
    return {}


@given("un proyecto MCP con .cognitive/")
def _mcp_project(mcp_wf_ctx: dict):
    mcp_wf_ctx["project_root"] = os.getcwd()


@when("invoco la tool MCP capture_task con objective Trabajo informal y files test.py")
def _call_capture(mcp_wf_ctx: dict):
    from features.cli_interface.mcp_adapter import _handle_request
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "capture_task", "arguments": {"_project_id": "walker", "objective": "Trabajo informal", "domain": "test", "files": ["test.py"]}},
    }
    mcp_wf_ctx["resp"] = _handle_request(msg)


@when("invoco la tool MCP submit vacía")
def _call_submit_empty(mcp_wf_ctx: dict):
    from features.cli_interface.mcp_adapter import _handle_request
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "submit", "arguments": {"project_id": "walker"}},
    }
    mcp_wf_ctx["resp"] = _handle_request(msg)


@when("invoco la tool MCP transition con gate no_existe")
def _call_transition_invalid(mcp_wf_ctx: dict):
    from features.cli_interface.mcp_adapter import _handle_request
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "transition", "arguments": {"project_id": "walker", "gate": "no_existe"}},
    }
    mcp_wf_ctx["resp"] = _handle_request(msg)


@then("la respuesta MCP es exitosa")
def _assert_mcp_ok(mcp_wf_ctx: dict):
    resp = mcp_wf_ctx.get("resp", {})
    assert resp.get("result", {}).get("isError") is not True, f"expected ok, got: {resp}"


@then("el resultado tiene type ok")
def _assert_type_ok(mcp_wf_ctx: dict):
    result = mcp_wf_ctx.get("resp", {}).get("result", {})
    content = result.get("content", [])
    text = content[0].get("text", "") if content else ""
    payload = json.loads(text) if text.startswith("{") else {}
    assert payload.get("status_code") == 200, f"expected status_code=200, got: {payload}"
    assert payload.get("data", {}).get("ok") is True, f"expected data.ok=True, got: {payload}"


@then("la respuesta MCP indica error")
def _assert_mcp_error(mcp_wf_ctx: dict):
    resp = mcp_wf_ctx.get("resp", {})
    result = resp.get("result", {})
    assert result.get("isError") is True or "error" in result.get("content", [{}])[0].get("text", ""), f"expected error, got: {resp}"
