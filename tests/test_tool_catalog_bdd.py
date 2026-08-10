"""Tests BDD (pytest-bdd) — Catálogo público de tools con outputSchema (ADR-043).

Feature value: el catálogo que alimenta `tools/list` proyecta outputSchema
para que el agente vea el contrato de salida de las tools (punto 4 del flujo:
ModuleRegistry → tool_catalog → tools/list).

Escenarios: tests/features/tool_catalog.feature
"""

from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/tool_catalog.feature")


@pytest.fixture
def catalog_ctx() -> dict:
    return {}


# ── Helpers ───────────────────────────────────────────────────────


def _registry_with_tool(output_schema: dict | None = None):
    from features.engine.infrastructure.module_registry import InMemoryModuleRegistry
    from features.engine.interface.contracts import ModuleDefinition, ToolDefinition

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
                        "properties": {"q": {"type": "string"}},
                    },
                    output_schema=output_schema,
                    metadata={"tags": ["demo"]},
                )
            ],
        )
    )
    return registry


# ── Given ─────────────────────────────────────────────────────────


@given("un ModuleRegistry con una tool demo con outputSchema declarado")
def _registry_with_output_schema(catalog_ctx: dict):
    schema = {
        "type": "object",
        "required": ["phase"],
        "properties": {"phase": {"const": "IMPLEMENT"}, "ok": {"type": "boolean"}},
    }
    catalog_ctx["registry"] = _registry_with_tool(output_schema=schema)


@given("un ModuleRegistry con una tool demo sin outputSchema")
def _registry_without_output_schema(catalog_ctx: dict):
    catalog_ctx["registry"] = _registry_with_tool(output_schema=None)


@given("el catálogo sin registry inyectado (bootstrap completo)")
def _catalog_bootstrap(catalog_ctx: dict):
    pass


# ── When ──────────────────────────────────────────────────────────


@when("listo las tools del catálogo")
def _list_tools(catalog_ctx: dict):
    from features.engine.application import tool_catalog

    registry = catalog_ctx.get("registry")
    if registry is not None:
        catalog_ctx["tools"] = tool_catalog.list_tools(registry)
    else:
        catalog_ctx["tools"] = tool_catalog.list_tools()


@when("obtengo la tool por nombre")
def _get_tool(catalog_ctx: dict):
    from features.engine.application import tool_catalog

    registry = catalog_ctx["registry"]
    catalog_ctx["tool"] = tool_catalog.get_tool("demo", registry)


# ── Then ──────────────────────────────────────────────────────────


@then("la tool demo tiene outputSchema")
def _has_output_schema(catalog_ctx: dict):
    tool = next((t for t in catalog_ctx["tools"] if t["name"] == "demo"), None)
    assert tool is not None, "demo no encontrada"
    assert "outputSchema" in tool, "demo sin outputSchema"


@then("la tool tiene outputSchema")
def _singular_has_output_schema(catalog_ctx: dict):
    tool = catalog_ctx.get("tool")
    assert tool is not None, "tool no obtenida"
    assert "outputSchema" in tool, f"{tool['name']} sin outputSchema"


@then("la tool demo tiene outputSchema vacío")
def _has_empty_output_schema(catalog_ctx: dict):
    tool = next((t for t in catalog_ctx["tools"] if t["name"] == "demo"), None)
    assert tool is not None, "demo no encontrada"
    assert tool["outputSchema"] == {}, f"outputSchema no vacío: {tool['outputSchema']}"


@then("el outputSchema coincide con el declarado")
def _matches_declared(catalog_ctx: dict):
    tool = catalog_ctx.get("tool") or next(
        (t for t in catalog_ctx["tools"] if t["name"] == "demo"), None
    )
    declared = {
        "type": "object",
        "required": ["phase"],
        "properties": {"phase": {"const": "IMPLEMENT"}, "ok": {"type": "boolean"}},
    }
    assert tool["outputSchema"] == declared, f"{tool['outputSchema']} != {declared}"


@then("las tasks de fase están presentes")
def _phase_tools_present(catalog_ctx: dict):
    names = [t["name"] for t in catalog_ctx["tools"]]
    for phase_tool in (
        "kg_task_context",
        "kg_diagnose",
        "kg_qa_evidence",
        "kg_sprint_evidence",
    ):
        assert phase_tool in names, f"{phase_tool} no en catálogo: {names}"


@then("kg_task_context proyecta outputSchema")
def _kg_task_context_schema(catalog_ctx: dict):
    entry = next(
        (t for t in catalog_ctx["tools"] if t["name"] == "kg_task_context"), None
    )
    assert entry is not None, "kg_task_context no encontrada"
    assert "outputSchema" in entry, "kg_task_context sin outputSchema"


@then("kg_diagnose proyecta outputSchema con propiedad diagnosis")
def _kg_diagnose_schema(catalog_ctx: dict):
    entry = next((t for t in catalog_ctx["tools"] if t["name"] == "kg_diagnose"), None)
    assert entry is not None, "kg_diagnose no encontrada"
    assert "outputSchema" in entry and entry["outputSchema"], (
        "kg_diagnose sin outputSchema"
    )
    assert "diagnosis" in entry["outputSchema"].get("properties", {}), entry[
        "outputSchema"
    ]
