"""
test_mcp_server_registry.py — Unit tests del McpServer + ToolRegistry integration (F3).

Cubre: shadowing bloqueado, SSOT unico (schema y dispatch del mismo registry),
register_tools adopción, y that tools/list == registry.
"""

import pytest


def test_mcp_server_blocks_duplicate():
    from libs.mcp_server import McpServer

    server = McpServer()
    ok1, errs1 = server.tool("status", "Status", handler=lambda a: {})
    assert ok1 and not errs1

    ok2, errs2 = server.tool("status", "Dupe", handler=lambda a: {})
    assert not ok2 and errs2
    assert "already registered" in errs2[0]


def test_mcp_server_blocks_empty_name():
    from libs.mcp_server import McpServer

    server = McpServer()
    ok, errs = server.tool("", "desc", handler=lambda a: {})
    assert not ok and errs


def test_mcp_server_register_tools_from_registry():
    """ToolRegistry (domain) adopta al McpServer como SSOT sin drift."""
    from libs.mcp_server import McpServer
    from features.cli_interface.domain.services import ToolRegistry

    registry = ToolRegistry()
    registry.register("status", "Status desc", handler=lambda a: {"ok": True})
    registry.register("submit", "Submit desc", handler=lambda a: {"ok": True})

    server = McpServer()
    count, errors = server.register_tools(registry)
    assert count == 2 and not errors

    tools = server.list_tools()
    assert len(tools) == 2
    names = {t["name"] for t in tools}
    assert names == {"status", "submit"}

    # SSOT: dispatch usa el mismo schema/handler
    result = server.call_tool("status", {})
    assert result == {"ok": True}


def test_registry_to_definition_matches_server():
    """tools/list del server == ToolDefinition del registry (cero drift)."""
    from libs.mcp_server import McpServer
    from features.cli_interface.domain.services import ToolRegistry

    registry = ToolRegistry()
    registry.register("ping", "Ping", input_schema={"type": "object"}, handler=lambda a: {"pong": True})

    server = McpServer()
    server.register_tools(registry)

    registry_defs = {d["name"]: d for d in registry.list_tools()}
    server_defs = {t["name"]: t for t in server.list_tools()}
    assert registry_defs["ping"]["inputSchema"] == server_defs["ping"]["inputSchema"]
    assert registry_defs["ping"]["description"] == server_defs["ping"]["description"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
