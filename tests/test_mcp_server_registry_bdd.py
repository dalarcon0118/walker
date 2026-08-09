"""Tests BDD (pytest-bdd) — McpServer registry.

Feature value: valida que el McpServer gestione herramientas sin duplicados
ni nombres vacíos, y que el ToolRegistry y el McpServer mantengan el mismo
schema (SSOT).

Escenarios: tests/features/mcp_server_registry.feature
"""
from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/mcp_server_registry.feature")


@pytest.fixture
def mcp_server_registry_ctx() -> dict:
    return {}


# ── Given ─────────────────────────────────────────────────────────────────────


@given("un McpServer vacío")
def _empty_server(mcp_server_registry_ctx: dict):
    from libs.mcp_server import McpServer

    mcp_server_registry_ctx["server"] = McpServer()


@given("un ToolRegistry con herramientas \"status\" y \"submit\"")
def _registry_with_tools(mcp_server_registry_ctx: dict):
    from features.cli_interface.domain.services import ToolRegistry

    registry = ToolRegistry()
    registry.register("status", "Status desc", handler=lambda a: {"ok": True})
    registry.register("submit", "Submit desc", handler=lambda a: {"ok": True})
    mcp_server_registry_ctx["registry"] = registry


@given("un ToolRegistry con la herramienta \"ping\" y un input_schema explícito")
def _registry_with_ping(mcp_server_registry_ctx: dict):
    from features.cli_interface.domain.services import ToolRegistry

    registry = ToolRegistry()
    registry.register(
        "ping", "Ping", input_schema={"type": "object"}, handler=lambda a: {"pong": True}
    )
    mcp_server_registry_ctx["registry"] = registry


# ── When ──────────────────────────────────────────────────────────────────────


@when("registro la herramienta \"status\"")
def _register_status(mcp_server_registry_ctx: dict):
    server = mcp_server_registry_ctx["server"]
    ok, errs = server.tool("status", "Status", handler=lambda a: {})
    mcp_server_registry_ctx["ok1"] = ok
    mcp_server_registry_ctx["errs1"] = errs


@when("intento registrar otra herramienta con el mismo nombre \"status\"")
def _register_duplicate(mcp_server_registry_ctx: dict):
    server = mcp_server_registry_ctx["server"]
    ok, errs = server.tool("status", "Dupe", handler=lambda a: {})
    mcp_server_registry_ctx["ok2"] = ok
    mcp_server_registry_ctx["errs2"] = errs


@when("intento registrar una herramienta con nombre vacío")
def _register_empty(mcp_server_registry_ctx: dict):
    server = mcp_server_registry_ctx["server"]
    ok, errs = server.tool("", "desc", handler=lambda a: {})
    mcp_server_registry_ctx["ok"] = ok
    mcp_server_registry_ctx["errs"] = errs


@when("registro todas las herramientas del ToolRegistry en el McpServer")
def _register_all_from_registry(mcp_server_registry_ctx: dict):
    server = mcp_server_registry_ctx["server"]
    registry = mcp_server_registry_ctx["registry"]
    count, errors = server.register_tools(registry)
    mcp_server_registry_ctx["count"] = count
    mcp_server_registry_ctx["errors"] = errors


@when("alisto las herramientas del McpServer")
def _list_tools_server(mcp_server_registry_ctx: dict):
    server = mcp_server_registry_ctx["server"]
    mcp_server_registry_ctx["tools"] = server.list_tools()


@when("despacho la herramienta \"status\"")
def _dispatch_status(mcp_server_registry_ctx: dict):
    server = mcp_server_registry_ctx["server"]
    mcp_server_registry_ctx["result"] = server.call_tool("status", {})


@when("registro la herramienta en el McpServer")
def _register_ping(mcp_server_registry_ctx: dict):
    server = mcp_server_registry_ctx["server"]
    registry = mcp_server_registry_ctx["registry"]
    server.register_tools(registry)
    mcp_server_registry_ctx["registry_defs"] = {
        d["name"]: d for d in registry.list_tools()
    }
    mcp_server_registry_ctx["server_defs"] = {
        t["name"]: t for t in server.list_tools()
    }


# ── Then ──────────────────────────────────────────────────────────────────────


@then("el segundo registro falla")
def _second_fails(mcp_server_registry_ctx: dict):
    assert not mcp_server_registry_ctx["ok2"]


@then("el error contiene \"already registered\"")
def _error_already_registered(mcp_server_registry_ctx: dict):
    assert "already registered" in mcp_server_registry_ctx["errs2"][0]


@then("el registro falla")
def _register_fails(mcp_server_registry_ctx: dict):
    assert not mcp_server_registry_ctx.get("ok", True) or mcp_server_registry_ctx.get("errs")


@then("el McpServer reporta 2 herramientas registradas")
def _count_2(mcp_server_registry_ctx: dict):
    assert mcp_server_registry_ctx["count"] == 2
    assert not mcp_server_registry_ctx["errors"]


@then("al listar las herramientas aparecen \"status\" y \"submit\"")
def _list_contains(mcp_server_registry_ctx: dict):
    tools = mcp_server_registry_ctx["tools"]
    assert len(tools) == 2
    names = {t["name"] for t in tools}
    assert names == {"status", "submit"}


@then("al despachar \"status\" se obtiene el resultado correcto")
def _dispatch_ok(mcp_server_registry_ctx: dict):
    result = mcp_server_registry_ctx["result"]
    assert result == {"ok": True}


@then("el inputSchema del server coincide con el del registry")
def _schema_matches(mcp_server_registry_ctx: dict):
    rdef = mcp_server_registry_ctx["registry_defs"]["ping"]
    sdef = mcp_server_registry_ctx["server_defs"]["ping"]
    assert rdef["inputSchema"] == sdef["inputSchema"]


@then("la descripción del server coincide con la del registry")
def _desc_matches(mcp_server_registry_ctx: dict):
    rdef = mcp_server_registry_ctx["registry_defs"]["ping"]
    sdef = mcp_server_registry_ctx["server_defs"]["ping"]
    assert rdef["description"] == sdef["description"]
