"""Tests BDD (pytest-bdd) — Dominio del Protocolo MCP (F1).

Feature value: VOs con validación, entities con ciclo de vida y aggregate
ToolRegistry como SSOT de tools/resources con invarianza de unicidad.

Escenarios: bdd/features/protocol_domain.feature
"""

from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/protocol_domain.feature")


@pytest.fixture
def protocol_ctx() -> dict:
    return {}


# ── Helpers ───────────────────────────────────────────────────────


def _create_tool_name(name: str):
    from features.cli_interface.domain.values import ToolName

    return ToolName.create(name)


def _protocol_version(version: str):
    from features.cli_interface.domain.values import ProtocolVersion

    return ProtocolVersion.create(version)


def _uri_pattern(uri: str):
    from features.cli_interface.domain.values import UriPattern

    return UriPattern.create(uri)


# ── Value Objects ─────────────────────────────────────────────────


@given("un validador de nombres de tool")
def _tool_name_validator(protocol_ctx: dict):
    pass


@when("creo un ToolName con un nombre válido")
def _create_valid_tool_name(protocol_ctx: dict):
    protocol_ctx["tool_name_result"] = _create_tool_name("kg_search")


@when("creo un ToolName con un nombre inválido")
def _create_invalid_tool_name(protocol_ctx: dict):
    protocol_ctx["tool_name_result"] = _create_tool_name("Bad Tool!")


@when("creo un ToolName con cadena vacía")
def _create_empty_tool_name(protocol_ctx: dict):
    protocol_ctx["tool_name_result"] = _create_tool_name("")


@then("el ToolName es válido")
def _tool_name_valid(protocol_ctx: dict):
    name, errors = protocol_ctx["tool_name_result"]
    assert name is not None and not errors


@then("el ToolName es rechazado")
def _tool_name_rejected(protocol_ctx: dict):
    name, errors = protocol_ctx["tool_name_result"]
    assert name is None and errors


@then("su valor es kg_search")
def _tool_name_value(protocol_ctx: dict):
    name, _ = protocol_ctx["tool_name_result"]
    assert name.value == "kg_search"


@then("el error indica Invalid tool name")
def _error_invalid_name(protocol_ctx: dict):
    _, errors = protocol_ctx["tool_name_result"]
    assert any("Invalid tool name" in e for e in errors), (
        f"Esperaba 'Invalid tool name' en {errors}"
    )


@then("el error indica cannot be empty")
def _error_empty(protocol_ctx: dict):
    _, errors = protocol_ctx["tool_name_result"]
    assert any("cannot be empty" in e for e in errors), (
        f"Esperaba 'cannot be empty' en {errors}"
    )


@given("un validador de versiones de protocolo")
def _version_validator(protocol_ctx: dict):
    pass


@when("creo una ProtocolVersion con una versión válida")
def _create_valid_version(protocol_ctx: dict):
    protocol_ctx["version_result"] = _protocol_version("2024-11-05")


@when("creo una ProtocolVersion con una versión moderna")
def _create_modern_version(protocol_ctx: dict):
    protocol_ctx["version_result"] = _protocol_version("2025-11-25")


@when("creo una ProtocolVersion con una versión antigua")
def _create_old_version(protocol_ctx: dict):
    protocol_ctx["version_result"] = _protocol_version("1999-01-01")


@then("la ProtocolVersion es válida")
def _version_valid(protocol_ctx: dict):
    v, errors = protocol_ctx["version_result"]
    assert v is not None and not errors


@then("la ProtocolVersion es rechazada")
def _version_rejected(protocol_ctx: dict):
    v, errors = protocol_ctx["version_result"]
    assert v is None and errors


@then("su valor es 2025-11-25")
def _version_value(protocol_ctx: dict):
    v, _ = protocol_ctx["version_result"]
    assert v.value == "2025-11-25"


@given("un validador de patrones URI")
def _uri_validator(protocol_ctx: dict):
    pass


@when("creo un UriPattern con una URI de walker")
def _create_valid_uri(protocol_ctx: dict):
    protocol_ctx["uri_result"] = _uri_pattern("walker://schema/{index}")


@when("creo un UriPattern con una URI HTTP")
def _create_http_uri(protocol_ctx: dict):
    protocol_ctx["uri_result"] = _uri_pattern("http://x")


@then("el UriPattern es válido")
def _uri_valid(protocol_ctx: dict):
    u, errors = protocol_ctx["uri_result"]
    assert u is not None and not errors


@then("el UriPattern es rechazado")
def _uri_rejected(protocol_ctx: dict):
    u, errors = protocol_ctx["uri_result"]
    assert u is None and errors


# ── Entities ─────────────────────────────────────────────────────


@given("una factory de sesiones MCP")
def _session_factory(protocol_ctx: dict):
    pass


@when("creo una McpSession")
def _create_session(protocol_ctx: dict):
    from features.cli_interface.domain.entities import McpSession
    from features.cli_interface.domain.values import SessionState

    protocol_ctx["session"], _ = McpSession.create()
    protocol_ctx["SessionState"] = SessionState


@then("la sesión está en estado UNINITIALIZED")
def _session_uninitialized(protocol_ctx: dict):
    from features.cli_interface.domain.values import SessionState

    assert protocol_ctx["session"].state == SessionState.UNINITIALIZED


@then("la sesión está en estado NEGOTIATED")
def _session_negotiated(protocol_ctx: dict):
    from features.cli_interface.domain.values import SessionState

    assert protocol_ctx["session"].state == SessionState.NEGOTIATED


@given("una McpSession sin inicializar")
def _uninit_session(protocol_ctx: dict):
    from features.cli_interface.domain.entities import McpSession

    protocol_ctx["session"], _ = McpSession.create()


@when("la inicializo con un cliente")
def _init_session(protocol_ctx: dict):
    protocol_ctx["init_errors"] = protocol_ctx["session"].initialize("test-client")


@then("se emite un evento SessionInitialized")
def _event_emitted(protocol_ctx: dict):
    events = protocol_ctx["session"].collect_events()
    assert any(type(e).__name__ == "SessionInitialized" for e in events), (
        "Esperaba SessionInitialized"
    )


@when("llamo a require_initialized")
def _require_initialized(protocol_ctx: dict):
    protocol_ctx["guard_errors"] = protocol_ctx["session"].require_initialized()


@then("la guarda falla con errores")
def _guard_fails(protocol_ctx: dict):
    assert protocol_ctx["guard_errors"], "La guarda debería fallar"


@given("una factory de tools")
def _tool_factory(protocol_ctx: dict):
    pass


@when("creo una Tool sin handler")
def _create_tool_no_handler(protocol_ctx: dict):
    from features.cli_interface.domain.entities import Tool

    protocol_ctx["tool_result"] = Tool.create("status", "desc", handler=None)


@then("la Tool es rechazada")
def _tool_rejected(protocol_ctx: dict):
    tool, errors = protocol_ctx["tool_result"]
    assert tool is None and errors


@given("una Tool con handler")
def _tool_with_handler(protocol_ctx: dict):
    from features.cli_interface.domain.entities import Tool

    protocol_ctx["tool"], _ = Tool.create("status", "desc", handler=lambda a: {})


@when("llamo a to_definition")
def _tool_to_definition(protocol_ctx: dict):
    protocol_ctx["definition"] = protocol_ctx["tool"].to_definition()


@then("la definición incluye el nombre")
def _def_has_name(protocol_ctx: dict):
    assert protocol_ctx["definition"]["name"] == "status"


@then("la definición incluye inputSchema")
def _def_has_schema(protocol_ctx: dict):
    assert "inputSchema" in protocol_ctx["definition"]


# ── Aggregate: ToolRegistry ──────────────────────────────────────


@given("un ToolRegistry vacío")
def _empty_registry(protocol_ctx: dict):
    from features.cli_interface.domain.services import ToolRegistry

    protocol_ctx["registry"] = ToolRegistry()


@given("un ToolRegistry con status registrada")
def _registry_with_status(protocol_ctx: dict):
    from features.cli_interface.domain.services import ToolRegistry

    reg = ToolRegistry()
    reg.register("status", "desc", handler=lambda a: {})
    protocol_ctx["registry"] = reg


@given("un ToolRegistry con ping registrada")
def _registry_with_ping(protocol_ctx: dict):
    from features.cli_interface.domain.services import ToolRegistry

    reg = ToolRegistry()
    reg.register("ping", "Ping", handler=lambda a: {"pong": True})
    protocol_ctx["registry"] = reg


@when("registro una tool")
def _register_tool(protocol_ctx: dict):
    reg = protocol_ctx["registry"]
    tool, errors = reg.register("a", "A desc", handler=lambda a: {})
    protocol_ctx["register_result"] = (tool, errors)


@when("registro la tool status")
def _register_status(protocol_ctx: dict):
    reg = protocol_ctx["registry"]
    protocol_ctx["register_result"] = reg.register(
        "status", "Status desc", handler=lambda a: {"ok": True}
    )


@when("intento registrar status nuevamente")
def _register_duplicate(protocol_ctx: dict):
    reg = protocol_ctx["registry"]
    protocol_ctx["register_result"] = reg.register(
        "status", "desc2", handler=lambda a: {}
    )


@when("despacho una tool desconocida")
def _dispatch_unknown(protocol_ctx: dict):
    reg = protocol_ctx["registry"]
    protocol_ctx["dispatch_result"] = reg.dispatch("nope", {})


@when("despacho ping")
def _dispatch_ping(protocol_ctx: dict):
    reg = protocol_ctx["registry"]
    protocol_ctx["dispatch_result"] = reg.dispatch("ping", {})


@when("registro un resource con URI de walker")
def _register_resource(protocol_ctx: dict):
    reg = protocol_ctx["registry"]
    res, errors = reg.register_resource(
        "walker://schema/{index}",
        "Index Schema",
        "Discover fields",
        "application/json",
        handler=lambda c: {},
    )
    protocol_ctx["register_result"] = (res, errors)


@then("la tool aparece en list_tools")
def _tool_in_list(protocol_ctx: dict):
    tools = protocol_ctx["registry"].list_tools()
    assert len(tools) >= 1, "No hay tools en el registry"


@then("su inputSchema es object-type")
def _tool_schema(protocol_ctx: dict):
    tools = protocol_ctx["registry"].list_tools()
    assert tools[0]["inputSchema"] == {"type": "object"}, tools[0]["inputSchema"]


@then("el registro falla")
def _register_fails(protocol_ctx: dict):
    tool, errors = protocol_ctx["register_result"]
    assert tool is None and errors


@then("el error indica already registered")
def _error_duplicate(protocol_ctx: dict):
    _, errors = protocol_ctx["register_result"]
    assert any("already registered" in e for e in errors), (
        f"Esperaba 'already registered' en {errors}"
    )


@then("el despacho falla con Unknown tool")
def _dispatch_fails(protocol_ctx: dict):
    result, errors = protocol_ctx["dispatch_result"]
    assert errors and any("Unknown tool" in e for e in errors)


@then("el despacho retorna un resultado exitoso")
def _dispatch_ok(protocol_ctx: dict):
    result, errors = protocol_ctx["dispatch_result"]
    assert not errors and result == {"pong": True}


@then("el resource aparece en list_resources")
def _resource_in_list(protocol_ctx: dict):
    resources = protocol_ctx["registry"].list_resources()
    assert len(resources) >= 1, "No hay resources"


@then("su URI es walker-schema")
def _resource_uri(protocol_ctx: dict):
    resources = protocol_ctx["registry"].list_resources()
    assert "walker://schema/" in resources[0]["uri"], resources[0]["uri"]


@then("se emite un evento ToolRegistered")
def _event_tool_registered(protocol_ctx: dict):
    events = protocol_ctx["registry"].collect_events()
    assert any(type(e).__name__ == "ToolRegistered" for e in events), (
        "Esperaba ToolRegistered"
    )


@then("al recolectar nuevamente no hay eventos pendientes")
def _no_pending_events(protocol_ctx: dict):
    assert protocol_ctx["registry"].collect_events() == []
