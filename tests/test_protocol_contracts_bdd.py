"""Tests BDD (pytest-bdd) — Protocol contracts.

Feature value: valida que los contratos del protocolo MCP validen
correctamente y que las respuestas y errores se serialicen bien.

Escenarios: tests/features/protocol_contracts.feature
"""
from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/protocol_contracts.feature")


@pytest.fixture
def protocol_contracts_ctx() -> dict:
    return {}


# ── Given ─────────────────────────────────────────────────────────────────────


@given('un ToolDefinition con nombre "kg_search"')
def _tool_def_kg_search(protocol_contracts_ctx: dict):
    from features.cli_interface.contracts.tool import ToolDefinition

    protocol_contracts_ctx["result"] = ToolDefinition.create(
        "kg_search", "Search memory", {"type": "object"}
    )


@given('un ToolDefinition con nombre vacío')
def _tool_def_empty(protocol_contracts_ctx: dict):
    from features.cli_interface.contracts.tool import ToolDefinition

    protocol_contracts_ctx["result"] = ToolDefinition.create("", "desc")


@given('un ToolDefinition válido')
def _tool_def_valid(protocol_contracts_ctx: dict):
    from features.cli_interface.contracts.tool import ToolDefinition

    dto, _ = ToolDefinition.create("status", "Status", {"type": "object"})
    protocol_contracts_ctx["dto"] = dto


@given('un JsonRpcRequest con método "tools/list"')
def _rpc_list(protocol_contracts_ctx: dict):
    from features.cli_interface.contracts.rpc import JsonRpcRequest

    protocol_contracts_ctx["req"], _ = JsonRpcRequest.create("tools/list", 1)


@given('un JsonRpcRequest con método "bogus/method"')
def _rpc_bogus(protocol_contracts_ctx: dict):
    from features.cli_interface.contracts.rpc import JsonRpcRequest

    protocol_contracts_ctx["req"], protocol_contracts_ctx["errors"] = JsonRpcRequest.create(
        "bogus/method", 1
    )


@given('un JsonRpcRequest con método "notifications/initialized"')
def _rpc_notification(protocol_contracts_ctx: dict):
    from features.cli_interface.contracts.rpc import JsonRpcRequest

    protocol_contracts_ctx["req"], _ = JsonRpcRequest.create(
        "notifications/initialized", None
    )


@given("params con protocolVersion y clientInfo")
def _init_params(protocol_contracts_ctx: dict):
    protocol_contracts_ctx["params"] = {
        "protocolVersion": "2024-11-05",
        "clientInfo": {"name": "opencode", "version": "1.0"},
    }


@given('params con name="status" y arguments={"x": 1}')
def _tool_call_params(protocol_contracts_ctx: dict):
    protocol_contracts_ctx["params"] = {"name": "status", "arguments": {"x": 1}}


@given('un JsonRpcResult con id=1 y result={"tools": []}')
def _rpc_result(protocol_contracts_ctx: dict):
    from features.cli_interface.contracts.rpc import JsonRpcResult

    protocol_contracts_ctx["r"] = JsonRpcResult(id=1, result={"tools": []})


@given('un JsonRpcError con code=-32601')
def _rpc_error(protocol_contracts_ctx: dict):
    from features.cli_interface.contracts.rpc import JsonRpcError

    protocol_contracts_ctx["e"] = JsonRpcError(id=1, code=-32601, message="Not supported")


@given('un Response de tipo "status"')
def _response_status(protocol_contracts_ctx: dict):
    from features.cli_interface.contracts.response import Response

    protocol_contracts_ctx["r"] = Response.ok("status", data={"node": "SPRINT"})


@given('un Response con mensaje "Not found"')
def _response_not_found(protocol_contracts_ctx: dict):
    from features.cli_interface.contracts.response import Response

    protocol_contracts_ctx["r"] = Response.error(
        "error", "Not found", status_code=404
    )


@given('un ErrorResponse con code=-32601')
def _error_response_ok(protocol_contracts_ctx: dict):
    from features.cli_interface.contracts.response import ErrorResponse

    protocol_contracts_ctx["result"] = ErrorResponse.create(-32601, "Method not found")


@given('un ErrorResponse con code=0 y mensaje vacío')
def _error_response_invalid(protocol_contracts_ctx: dict):
    from features.cli_interface.contracts.response import ErrorResponse

    protocol_contracts_ctx["result"] = ErrorResponse.create(0, "")


@given('una NextAction con run="walker submit"')
def _next_action(protocol_contracts_ctx: dict):
    from features.cli_interface.contracts.response import NextAction

    protocol_contracts_ctx["na"] = NextAction(run="walker submit", description="Advance")


# ── When ──────────────────────────────────────────────────────────────────────


@when("creo el DTO")
def _create_tool(protocol_contracts_ctx: dict):
    result = protocol_contracts_ctx["result"]
    if isinstance(result, tuple):
        protocol_contracts_ctx["dto"], protocol_contracts_ctx["errors"] = result
    else:
        protocol_contracts_ctx.setdefault("errors", [])


@when("creo la request")
def _create_rpc(protocol_contracts_ctx: dict):
    result = protocol_contracts_ctx["req"]
    if isinstance(result, tuple):
        protocol_contracts_ctx["req"], protocol_contracts_ctx["errors"] = result
    else:
        protocol_contracts_ctx.setdefault("errors", [])


@when("creo InitializeRequest")
def _create_init(protocol_contracts_ctx: dict):
    from features.cli_interface.contracts.rpc import InitializeRequest

    protocol_contracts_ctx["req"] = InitializeRequest.from_params(
        protocol_contracts_ctx["params"]
    )


@when("creo ToolCallRequest")
def _create_tool_call(protocol_contracts_ctx: dict):
    from features.cli_interface.contracts.rpc import ToolCallRequest

    protocol_contracts_ctx["req"] = ToolCallRequest.from_params(
        protocol_contracts_ctx["params"]
    )


@when("lo convierto a dict")
def _convert_tool(protocol_contracts_ctx: dict):
    dto = protocol_contracts_ctx.get("dto") or protocol_contracts_ctx.get("r") or protocol_contracts_ctx.get("e") or protocol_contracts_ctx.get("na")
    protocol_contracts_ctx["d"] = dto.to_dict()


@when("creo la respuesta ok")
def _response_ok(protocol_contracts_ctx: dict):
    from features.cli_interface.contracts.response import Response

    r = protocol_contracts_ctx["r"]
    if isinstance(r, Response):
        protocol_contracts_ctx["result"] = r
    else:
        protocol_contracts_ctx["result"] = Response.ok(
            "status", data={"node": "SPRINT"}
        )


@when("creo la respuesta error con 404")
def _response_error_404(protocol_contracts_ctx: dict):
    from features.cli_interface.contracts.response import Response

    protocol_contracts_ctx["result"] = Response.error(
        "error", "Not found", status_code=404
    )


@when("creo el error")
def _create_error_response(protocol_contracts_ctx: dict):
    result = protocol_contracts_ctx["result"]
    if isinstance(result, tuple):
        protocol_contracts_ctx["dto"], protocol_contracts_ctx["errors"] = result
    else:
        protocol_contracts_ctx.setdefault("errors", [])


@when("la convierto a dict")
def _convert_na(protocol_contracts_ctx: dict):
    protocol_contracts_ctx["d"] = protocol_contracts_ctx["na"].to_dict()


# ── Then ──────────────────────────────────────────────────────────────────────


@then("el DTO es válido")
def _dto_valid(protocol_contracts_ctx: dict):
    dto, errors = protocol_contracts_ctx["result"]
    assert dto is not None and not errors


@then('el nombre es "kg_search"')
def _name_kg_search(protocol_contracts_ctx: dict):
    assert protocol_contracts_ctx["dto"].name == "kg_search"


@then("el DTO es None")
def _dto_none(protocol_contracts_ctx: dict):
    dto, errors = protocol_contracts_ctx["result"]
    assert dto is None and errors


@then('el error contiene "cannot be empty"')
def _cannot_be_empty(protocol_contracts_ctx: dict):
    assert "cannot be empty" in protocol_contracts_ctx["errors"][0]


@then('name es "status"')
def _name_status(protocol_contracts_ctx: dict):
    if "dto" in protocol_contracts_ctx:
        assert protocol_contracts_ctx["dto"].name == "status"
    else:
        assert protocol_contracts_ctx["req"].name == "status"


@then('inputSchema es {"type": "object"}')
def _input_schema_object(protocol_contracts_ctx: dict):
    assert protocol_contracts_ctx["d"]["inputSchema"] == {"type": "object"}


@then('el método es "tools/list"')
def _method_list(protocol_contracts_ctx: dict):
    assert protocol_contracts_ctx["req"].method.value == "tools/list"


@then("la request es None")
def _req_none(protocol_contracts_ctx: dict):
    req, errors = protocol_contracts_ctx["req"], protocol_contracts_ctx["errors"]
    assert req is None and errors


@then('el error contiene "Unsupported method"')
def _unsupported_method(protocol_contracts_ctx: dict):
    assert "Unsupported method" in protocol_contracts_ctx["errors"][0]


@then("es notificación")
def _is_notification(protocol_contracts_ctx: dict):
    assert protocol_contracts_ctx["req"].is_notification


@then('protocol_version es "2024-11-05"')
def _proto_version(protocol_contracts_ctx: dict):
    assert protocol_contracts_ctx["req"].protocol_version == "2024-11-05"


@then('client_name es "opencode"')
def _client_name(protocol_contracts_ctx: dict):
    assert protocol_contracts_ctx["req"].client_name == "opencode"


@then('arguments es {"x": 1}')
def _tc_args(protocol_contracts_ctx: dict):
    assert protocol_contracts_ctx["req"].arguments == {"x": 1}


@then('jsonrpc es "2.0"')
def _jsonrpc_2(protocol_contracts_ctx: dict):
    assert protocol_contracts_ctx["d"]["jsonrpc"] == "2.0"


@then("id es 1")
def _id_1(protocol_contracts_ctx: dict):
    assert protocol_contracts_ctx["d"]["id"] == 1


@then('error.code es -32601')
def _error_code(protocol_contracts_ctx: dict):
    assert protocol_contracts_ctx["d"]["error"]["code"] == -32601


@then('status_code es 200')
def _status_200(protocol_contracts_ctx: dict):
    assert protocol_contracts_ctx["result"].status_code == 200


@then('type es "status"')
def _type_status(protocol_contracts_ctx: dict):
    assert protocol_contracts_ctx["result"].type == "status"


@then('status_code es 404')
def _status_404(protocol_contracts_ctx: dict):
    assert protocol_contracts_ctx["result"].status_code == 404


@then('message es "Not found"')
def _message_not_found(protocol_contracts_ctx: dict):
    assert protocol_contracts_ctx["result"].to_dict()["message"] == "Not found"


@then("es válido")
def _error_valid(protocol_contracts_ctx: dict):
    dto, errors = protocol_contracts_ctx["result"]
    assert dto is not None and not errors


@then("es None y tiene errores")
def _error_invalid(protocol_contracts_ctx: dict):
    dto, errors = protocol_contracts_ctx["result"]
    assert dto is None and errors


@then('run es "walker submit"')
def _run_walker_submit(protocol_contracts_ctx: dict):
    assert protocol_contracts_ctx["d"]["run"] == "walker submit"


@then('description es "Advance"')
def _description_advance(protocol_contracts_ctx: dict):
    assert protocol_contracts_ctx["d"]["description"] == "Advance"
