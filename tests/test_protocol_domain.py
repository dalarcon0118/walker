"""
test_protocol_domain.py — Unit tests del Bounded Context "Protocol" (F1).

Cubre: VOs con validación, entities (Tool/McpSession), aggregate ToolRegistry
(invarianzas de unicidad + SSOT schema/handler) y eventos.
"""

import pytest


# ── Value Objects ──

def test_tool_name_create_valid():
    from features.cli_interface.domain.values import ToolName
    name, errors = ToolName.create("kg_search")
    assert name is not None and not errors
    assert name.value == "kg_search"


def test_tool_name_create_invalid():
    from features.cli_interface.domain.values import ToolName
    name, errors = ToolName.create("Bad Tool!")
    assert name is None and errors
    assert "Invalid tool name" in errors[0]


def test_tool_name_create_empty():
    from features.cli_interface.domain.values import ToolName
    name, errors = ToolName.create("")
    assert name is None and errors
    assert "cannot be empty" in errors[0]


def test_protocol_version_valid():
    from features.cli_interface.domain.values import ProtocolVersion
    v, errors = ProtocolVersion.create("2024-11-05")
    assert v is not None and not errors


def test_protocol_version_invalid():
    from features.cli_interface.domain.values import ProtocolVersion
    v, errors = ProtocolVersion.create("1999-01-01")
    assert v is None and errors


def test_uri_pattern_valid():
    from features.cli_interface.domain.values import UriPattern
    u, errors = UriPattern.create("walker://schema/{index}")
    assert u is not None and not errors


def test_uri_pattern_invalid():
    from features.cli_interface.domain.values import UriPattern
    u, errors = UriPattern.create("http://x")
    assert u is None and errors


def test_json_rpc_id_notification():
    from features.cli_interface.domain.values import JsonRpcId
    nid = JsonRpcId(None)
    assert nid.is_notification
    rid = JsonRpcId(3)
    assert not rid.is_notification


# ── Entities ──

def test_session_create_initialize():
    from features.cli_interface.domain.entities import McpSession
    from features.cli_interface.domain.values import SessionState
    session, errors = McpSession.create()
    assert session is not None and not errors
    assert session.state == SessionState.UNINITIALIZED

    errs = session.initialize("test-client")
    assert not errs
    assert session.state == SessionState.NEGOTIATED
    events = session.collect_events()
    assert len(events) == 1
    assert type(events[0]).__name__ == "SessionInitialized"


def test_session_requires_initialized():
    from features.cli_interface.domain.entities import McpSession
    session, _ = McpSession.create()
    errors = session.require_initialized()
    assert errors, "uninitialized session must fail guard"


def test_tool_create_requires_handler():
    from features.cli_interface.domain.entities import Tool
    tool, errors = Tool.create("status", "desc", handler=None)
    assert tool is None and errors


def test_tool_to_definition():
    from features.cli_interface.domain.entities import Tool
    tool, _ = Tool.create("status", "desc", handler=lambda a: {})
    assert tool is not None
    d = tool.to_definition()
    assert d["name"] == "status"
    assert "inputSchema" in d


# ── Aggregate: ToolRegistry ──

def test_registry_register_and_list():
    from features.cli_interface.domain.services import ToolRegistry
    reg = ToolRegistry()
    tool, errors = reg.register("status", "Status desc", handler=lambda a: {"ok": True})
    assert tool is not None and not errors
    tools = reg.list_tools()
    assert len(tools) == 1
    assert tools[0]["name"] == "status"
    assert tools[0]["inputSchema"] == {"type": "object"}


def test_registry_blocks_duplicate():
    from features.cli_interface.domain.services import ToolRegistry
    reg = ToolRegistry()
    reg.register("status", "desc", handler=lambda a: {})
    tool, errors = reg.register("status", "desc2", handler=lambda a: {})
    assert tool is None and errors
    assert "already registered" in errors[0]


def test_registry_dispatch_unknown():
    from features.cli_interface.domain.services import ToolRegistry
    reg = ToolRegistry()
    result, errors = reg.dispatch("nope", {})
    assert errors and "Unknown tool" in errors[0]


def test_registry_dispatch_ok():
    from features.cli_interface.domain.services import ToolRegistry
    reg = ToolRegistry()
    reg.register("ping", "Ping", handler=lambda a: {"pong": True})
    result, errors = reg.dispatch("ping", {})
    assert not errors and result == {"pong": True}


def test_registry_register_resource():
    from features.cli_interface.domain.services import ToolRegistry
    reg = ToolRegistry()
    res, errors = reg.register_resource(
        "walker://schema/{index}", "Index Schema", "Discover fields",
        "application/json", handler=lambda c: {},
    )
    assert res is not None and not errors
    resources = reg.list_resources()
    assert len(resources) == 1
    assert resources[0]["uri"] == "walker://schema/{index}"


def test_registry_collect_events():
    from features.cli_interface.domain.services import ToolRegistry
    reg = ToolRegistry()
    reg.register("a", "A", handler=lambda a: {})
    events = reg.collect_events()
    assert len(events) == 1
    assert type(events[0]).__name__ == "ToolRegistered"
    assert reg.collect_events() == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
