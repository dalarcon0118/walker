"""Tests BDD (pytest-bdd) — MCP Adapter JSON-RPC 2.0.

Escenarios: bdd/features/mcp_adapter.feature
"""

from __future__ import annotations

import json

import pytest
from pytest_bdd import given, scenarios, then

scenarios("bdd/features/mcp_adapter.feature")


@pytest.fixture
def mcp_ctx() -> dict:
    return {}


@given("que invoco _handle_request con initialize", target_fixture="mcp_ctx")
def _initialize(mcp_ctx: dict):
    from features.cli_interface.mcp_adapter import _handle_request

    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"clientInfo": {"name": "test"}, "protocolVersion": "2024-11-05"},
    }
    mcp_ctx["resp"] = _handle_request(msg)
    return mcp_ctx


@given("que invoco _handle_request con tools/list", target_fixture="mcp_ctx")
def _tools_list(mcp_ctx: dict):
    from features.cli_interface.mcp_adapter import _handle_request

    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    mcp_ctx["resp"] = _handle_request(msg)
    return mcp_ctx


@given("que invoco _handle_request con tools/call status", target_fixture="mcp_ctx")
def _tools_call_status(mcp_ctx: dict):
    from features.cli_interface.mcp_adapter import _handle_request

    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "status", "arguments": {}},
    }
    mcp_ctx["resp"] = _handle_request(msg)
    return mcp_ctx


@given("que invoco _handle_request con tools/call no_existe", target_fixture="mcp_ctx")
def _tools_call_missing(mcp_ctx: dict):
    from features.cli_interface.mcp_adapter import _handle_request

    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "no_existe", "arguments": {}},
    }
    mcp_ctx["resp"] = _handle_request(msg)
    return mcp_ctx


@given("que invoco _handle_request con ping", target_fixture="mcp_ctx")
def _ping(mcp_ctx: dict):
    from features.cli_interface.mcp_adapter import _handle_request

    msg = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    mcp_ctx["resp"] = _handle_request(msg)
    return mcp_ctx


@given("que invoco _handle_request con no_soportado", target_fixture="mcp_ctx")
def _unsupported(mcp_ctx: dict):
    from features.cli_interface.mcp_adapter import _handle_request

    msg = {"jsonrpc": "2.0", "id": 1, "method": "no_soportado"}
    mcp_ctx["resp"] = _handle_request(msg)
    return mcp_ctx


@then("la respuesta tiene jsonrpc 2.0")
def _assert_jsonrpc(mcp_ctx: dict):
    assert mcp_ctx["resp"].get("jsonrpc") == "2.0"


@then("la respuesta tiene id")
def _assert_has_id(mcp_ctx: dict):
    assert "id" in mcp_ctx["resp"]


@then("capabilities existe")
def _assert_capabilities(mcp_ctx: dict):
    result = mcp_ctx["resp"].get("result", {})
    assert "capabilities" in result
    assert "tools" in result["capabilities"]


@then("serverInfo es walker")
def _assert_server_name(mcp_ctx: dict):
    result = mcp_ctx["resp"].get("result", {})
    assert result.get("serverInfo", {}).get("name") == "walker"


@then("la respuesta tiene result.tools")
def _assert_result_tools(mcp_ctx: dict):
    assert "result" in mcp_ctx["resp"]
    assert "tools" in mcp_ctx["resp"]["result"]


@then("result.tools es una lista no vacía")
def _assert_tools_non_empty(mcp_ctx: dict):
    tools = mcp_ctx["resp"].get("result", {}).get("tools", [])
    assert isinstance(tools, list)
    assert len(tools) > 0


@then("tipo es text")
def _assert_content_type(mcp_ctx: dict):
    content = mcp_ctx["resp"].get("result", {}).get("content", [])
    assert content
    assert content[0].get("type") == "text"


@then("texto es JSON parseable")
def _assert_content_json(mcp_ctx: dict):
    content = mcp_ctx["resp"].get("result", {}).get("content", [])
    assert content
    text = content[0].get("text", "")
    parsed = json.loads(text)
    assert isinstance(parsed, dict)


@then("la respuesta indica error")
def _assert_indicates_error(mcp_ctx: dict):
    result = mcp_ctx["resp"].get("result", {})
    content = result.get("content", [])
    text = content[0].get("text", "") if content else ""
    assert '"error"' in text or result.get("isError") is True


@then("la respuesta tiene error")
def _assert_has_error(mcp_ctx: dict):
    assert "error" in mcp_ctx["resp"]


@then("la respuesta tiene result vacío")
def _assert_result_empty(mcp_ctx: dict):
    result = mcp_ctx["resp"].get("result", {})
    assert result == {}
