"""Tests BDD (pytest-bdd) — Catálogo público de tools con outputSchema (ADR-043).

Feature value: el catálogo que alimenta `tools/list` proyecta outputSchema
para que el agente vea el contrato de salida de las tools (punto 4 del flujo:
ModuleRegistry → tool_catalog → tools/list).

Escenarios: bdd/features/tool_catalog.feature
"""

from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/tool_catalog.feature")


@pytest.fixture
def catalog_ctx() -> dict:
    return {}


# ── Helpers ───────────────────────────────────────────────────────


def _registry_with_tool(output_schema: dict | None = None):
    from shared.libs.module_system.registry import InMemoryModuleRegistry
    from shared.libs.module_system.contracts import ModuleDefinition, ToolDefinition

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
    # DI (ADR-058/059): ya no existe un default `get_module_registry()`. El
    # catálogo completo del bootstrap se obtiene inyectando el module_registry
    # del composition root (AppContainer.build) — el mismo path que usa el MCP.
    from app.container import AppContainer

    app = AppContainer.build()
    catalog_ctx["registry"] = getattr(app, "module_registry", None)


# ── When ──────────────────────────────────────────────────────────


@when("listo las tools del catálogo")
def _list_tools(catalog_ctx: dict):
    from shared.core.engine.application import tool_catalog

    registry = catalog_ctx.get("registry")
    if registry is not None:
        catalog_ctx["tools"] = tool_catalog.list_tools(registry)
    else:
        catalog_ctx["tools"] = tool_catalog.list_tools()


@when("obtengo la tool por nombre")
def _get_tool(catalog_ctx: dict):
    from shared.core.engine.application import tool_catalog

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
        "task_context",
        "diagnose_failure",
        "verify_evidence",
        "sprint_evidence",
    ):
        assert phase_tool in names, f"{phase_tool} no en catálogo: {names}"


@then("kg_task_context proyecta outputSchema")
def _kg_task_context_schema(catalog_ctx: dict):
    entry = next(
        (t for t in catalog_ctx["tools"] if t["name"] == "task_context"), None
    )
    assert entry is not None, "task_context no encontrada"
    assert "outputSchema" in entry, "task_context sin outputSchema"


@then("kg_diagnose proyecta outputSchema con propiedad diagnosis")
def _kg_diagnose_schema(catalog_ctx: dict):
    entry = next((t for t in catalog_ctx["tools"] if t["name"] == "diagnose_failure"), None)
    assert entry is not None, "diagnose_failure no encontrada"
    assert "outputSchema" in entry and entry["outputSchema"], (
        "diagnose_failure sin outputSchema"
    )
    assert "diagnosis" in entry["outputSchema"].get("properties", {}), entry[
        "outputSchema"
    ]
