"""Tests BDD (pytest-bdd) — Help y REST derivados del registro SSOT (F4).

Feature value: walker://help y las rutas REST se generan desde la metadata del
registro, no desde texto fijo, garantizando un único fuente de verdad.

Escenarios: bdd/features/f4_help_rest.feature
"""
from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/f4_help_rest.feature")


@pytest.fixture
def f4_ctx() -> dict:
    return {}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _registry_with_tool(example=None):
    from shared.core.engine.infrastructure.module_registry import InMemoryModuleRegistry
    from shared.core.engine.interface.contracts import ModuleDefinition, ToolDefinition

    meta = {}
    if example is not None:
        meta["example"] = example
    registry = InMemoryModuleRegistry()
    registry.register_module(
        ModuleDefinition(
            name="test",
            tools=[
                ToolDefinition(
                    name="demo",
                    description="Demo tool",
                    handler=lambda a: {"ok": True},
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                    output_schema={
                        "type": "object",
                        "properties": {"ok": {"type": "boolean"}},
                    },
                    metadata=meta,
                )
            ],
        )
    )
    return registry


# ── Help ─────────────────────────────────────────────────────────────────────


@given("un tool registrado con description, example y schemas")
def _registered_tool_with_metadata(f4_ctx: dict):
    f4_ctx["registry"] = _registry_with_tool(example="walker <p>/demo '{...}'")


@when("se construye la ayuda del tool")
def _build_tool_help(f4_ctx: dict):
    from shared.core.engine.application import tool_catalog

    f4_ctx["help_doc"] = tool_catalog.build_tool_help("demo", f4_ctx["registry"])


@then("el documento de ayuda tiene el nombre correcto")
def _assert_help_name(f4_ctx: dict):
    assert f4_ctx["help_doc"]["name"] == "demo"


@then("la description se deriva del registro")
def _assert_help_description(f4_ctx: dict):
    assert f4_ctx["help_doc"]["description"] == "Demo tool"


@then("el example se deriva del registro")
def _assert_help_example(f4_ctx: dict):
    assert f4_ctx["help_doc"]["example"] == "walker <p>/demo '{...}'"


@then("los schemas de input y output se derivan como dicts")
def _assert_help_schemas(f4_ctx: dict):
    doc = f4_ctx["help_doc"]
    assert doc["input_schema"]["properties"]["query"]["type"] == "string"
    assert doc["output_schema"]["properties"]["ok"]["type"] == "boolean"


@given("un registro con una tool demo")
def _registry_with_demo(f4_ctx: dict):
    f4_ctx["registry"] = _registry_with_tool()


@when("se pide ayuda de un tool que no existe")
def _ask_help_for_missing(f4_ctx: dict):
    from shared.core.engine.application import tool_catalog

    f4_ctx["help_doc"] = tool_catalog.build_tool_help("no_existe", f4_ctx["registry"])


@then("el resultado es None (respuesta 404, no texto inventado)")
def _assert_help_none(f4_ctx: dict):
    assert f4_ctx["help_doc"] is None


@given("un documento de ayuda con todos los campos")
def _help_doc_with_all_fields(f4_ctx: dict):
    f4_ctx["doc"] = {
        "name": "demo",
        "description": "Demo description",
        "usage": "walker demo",
        "example": "usage demo",
        "input_schema": {
            "type": "object",
            "properties": {"x": {"type": "string"}},
        },
        "output_schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
        },
    }


@when("se renderiza el texto de ayuda")
def _render_help_text(f4_ctx: dict):
    from features.main.cli_interface.mcp_adapter import _render_help_text

    f4_ctx["text"] = _render_help_text(f4_ctx["doc"])


@then("el texto contiene la description")
def _assert_text_description(f4_ctx: dict):
    assert "Demo description" in f4_ctx["text"]


@then("el texto contiene el usage")
def _assert_text_usage(f4_ctx: dict):
    assert "walker demo" in f4_ctx["text"]


@then("contiene el usage")
def _assert_contains_usage(f4_ctx: dict):
    assert "walker demo" in f4_ctx["text"]


@then("contiene el example")
def _assert_contains_example(f4_ctx: dict):
    assert "usage demo" in f4_ctx["text"]


@then("el texto contiene el example")
def _assert_text_example(f4_ctx: dict):
    assert "usage demo" in f4_ctx["text"]


@then("contiene las propiedades de los schemas")
def _assert_contains_schema_props(f4_ctx: dict):
    text = f4_ctx["text"]
    assert '"properties"' in text
    assert '"x"' in text
    assert '"ok"' in text


# ── Resources ────────────────────────────────────────────────────────────────


@given("un servidor MCP construido")
def _built_server(f4_ctx: dict):
    from features.main.cli_interface.mcp_adapter import _build_server

    f4_ctx["server"] = _build_server()


@given("un servidor MCP con tools registradas")
def _built_server_with_tools(f4_ctx: dict):
    from features.main.cli_interface.mcp_adapter import _build_server

    f4_ctx["server"] = _build_server()


@when("se listan los recursos")
def _list_resources(f4_ctx: dict):
    f4_ctx["resources"] = f4_ctx["server"].list_resources()


@then("existe el recurso walker://help/{tool}")
def _assert_help_resource_exists(f4_ctx: dict):
    assert any(
        r["uri"] == "walker://help/{tool}" for r in f4_ctx["resources"]
    ), f4_ctx["resources"]


@when("se lee el recurso walker://help/kg_diagnose")
def _read_help_kg_diagnose(f4_ctx: dict):
    f4_ctx["result"] = f4_ctx["server"].read_resource("walker://help/kg_diagnose")


@then("la respuesta tiene contents con uri walker://help/kg_diagnose")
def _assert_result_contents_uri(f4_ctx: dict):
    result = f4_ctx["result"]
    assert "contents" in result
    contents = result["contents"][0]
    assert contents.get("uri") == "walker://help/kg_diagnose"


@then("el texto contiene # Tool: kg_diagnose")
def _assert_text_tool_name(f4_ctx: dict):
    text = f4_ctx["result"]["contents"][0].get("text", "")
    assert "# Tool: kg_diagnose" in text


@then("el texto contiene Output schema")
def _assert_text_output_schema(f4_ctx: dict):
    text = f4_ctx["result"]["contents"][0].get("text", "")
    assert "Output schema" in text


@when("se lee el recurso de un tool inexistente")
def _read_help_missing_tool(f4_ctx: dict):
    f4_ctx["result"] = f4_ctx["server"].read_resource(
        "walker://help/tool_inexistente"
    )


@then("la respuesta contiene error con not found")
def _assert_error_not_found(f4_ctx: dict):
    result = f4_ctx["result"]
    assert "error" in result
    assert "not found" in result["error"]


# ── REST ─────────────────────────────────────────────────────────────────────


@given("una REST route con flags_builder")
def _rest_route_with_flags_builder(f4_ctx: dict):
    from shared.core.engine.interface.contracts import RestRoute

    f4_ctx["route"] = RestRoute(
        path="{project_id}/submit",
        method="POST",
        flags_builder=lambda a: {"_payload": a.get("body", a)},
    )


@when("se invoca con body status: submitted")
def _invoke_flags_builder(f4_ctx: dict):
    f4_ctx["flags"] = f4_ctx["route"].flags_builder(
        {"body": {"status": "submitted"}}
    )


@then("el flags result es el payload declarado sin campos nuevos")
def _assert_flags_declared(f4_ctx: dict):
    assert f4_ctx["flags"] == {"_payload": {"status": "submitted"}}


@given("una REST route sin flags_builder")
def _rest_route_without_flags_builder(f4_ctx: dict):
    from shared.core.engine.interface.contracts import RestRoute

    f4_ctx["route"] = RestRoute(path="{project_id}/status", method="GET")


@when("se invoca con un body")
def _invoke_default_flags(f4_ctx: dict):
    f4_ctx["flags2"] = f4_ctx["route"].flags_builder({"body": {"a": 1}})


@then("el flags result es el payload original sin modificación")
def _assert_flags_original(f4_ctx: dict):
    assert f4_ctx["flags2"] == {"_payload": {"body": {"a": 1}}}
