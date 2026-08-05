"""
test_protocol_contracts.py — Unit tests de los contracts (F2).

Cubre: ToolDefinition (schema del agente), JsonRpc DTOs, Response/NextAction.
"""

import pytest


# ── contracts/tool.py ──

def test_tool_definition_create():
    from features.cli_interface.contracts.tool import ToolDefinition
    dto, errors = ToolDefinition.create("kg_search", "Search memory", {"type": "object"})
    assert dto is not None and not errors
    assert dto.name == "kg_search"


def test_tool_definition_invalid():
    from features.cli_interface.contracts.tool import ToolDefinition
    dto, errors = ToolDefinition.create("", "desc")
    assert dto is None and errors
    assert "cannot be empty" in errors[0]


def test_tool_definition_to_dict():
    from features.cli_interface.contracts.tool import ToolDefinition
    dto, _ = ToolDefinition.create("status", "Status", {"type": "object"})
    d = dto.to_dict()
    assert d["name"] == "status"
    assert d["inputSchema"] == {"type": "object"}


def test_resource_definition_to_dict():
    from features.cli_interface.contracts.tool import ResourceDefinition
    res = ResourceDefinition("walker://schema/ckg", "CKG Schema")
    d = res.to_dict()
    assert d["uri"] == "walker://schema/ckg"
    assert d["mimeType"] == "application/json"


# ── contracts/rpc.py ──

def test_json_rpc_request_create_valid():
    from features.cli_interface.contracts.rpc import JsonRpcRequest
    req, errors = JsonRpcRequest.create("tools/list", 1)
    assert req is not None and not errors
    assert req.method.value == "tools/list"


def test_json_rpc_request_create_invalid_method():
    from features.cli_interface.contracts.rpc import JsonRpcRequest
    req, errors = JsonRpcRequest.create("bogus/method", 1)
    assert req is None and errors
    assert "Unsupported method" in errors[0]


def test_json_rpc_request_notification():
    from features.cli_interface.contracts.rpc import JsonRpcRequest
    req, _ = JsonRpcRequest.create("notifications/initialized", None)
    assert req is not None and req.is_notification


def test_initialize_request_from_params():
    from features.cli_interface.contracts.rpc import InitializeRequest
    req = InitializeRequest.from_params({
        "protocolVersion": "2024-11-05",
        "clientInfo": {"name": "opencode", "version": "1.0"},
    })
    assert req.protocol_version == "2024-11-05"
    assert req.client_name == "opencode"


def test_tool_call_request_from_params():
    from features.cli_interface.contracts.rpc import ToolCallRequest
    req = ToolCallRequest.from_params({"name": "status", "arguments": {"x": 1}})
    assert req.name == "status"
    assert req.arguments == {"x": 1}


def test_json_rpc_result_to_dict():
    from features.cli_interface.contracts.rpc import JsonRpcResult
    r = JsonRpcResult(id=1, result={"tools": []})
    d = r.to_dict()
    assert d["jsonrpc"] == "2.0" and d["id"] == 1


def test_json_rpc_error_to_dict():
    from features.cli_interface.contracts.rpc import JsonRpcError
    e = JsonRpcError(id=1, code=-32601, message="Not supported")
    d = e.to_dict()
    assert d["error"]["code"] == -32601


# ── contracts/response.py ──

def test_response_ok():
    from features.cli_interface.contracts.response import Response
    r = Response.ok("status", data={"node": "SPRINT"})
    assert r.status_code == 200 and r.type == "status"
    assert r.to_dict()["status_code"] == 200


def test_response_error():
    from features.cli_interface.contracts.response import Response
    r = Response.error("error", "Not found", status_code=404)
    assert r.status_code == 404
    assert r.to_dict()["message"] == "Not found"


def test_error_response_create():
    from features.cli_interface.contracts.response import ErrorResponse
    e, errors = ErrorResponse.create(-32601, "Method not found")
    assert e is not None and not errors


def test_error_response_invalid():
    from features.cli_interface.contracts.response import ErrorResponse
    e, errors = ErrorResponse.create(0, "")
    assert e is None and errors


def test_next_action_to_dict():
    from features.cli_interface.contracts.response import NextAction
    na = NextAction(run="walker submit", description="Advance")
    d = na.to_dict()
    assert d["run"] == "walker submit"
    assert d["description"] == "Advance"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
