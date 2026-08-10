"""Tests BDD (pytest-bdd) — Equivalencia MCP tools/list vs registry (ADR-043).

Feature value: garantiza que el MCP tools/list y el registry.list_tasks
no tengan drift (sin tools fantasma ni invisibles).

Escenarios: tests/features/mcp_tools_list_equivalence.feature
"""

from __future__ import annotations


import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/mcp_tools_list_equivalence.feature")


@pytest.fixture
def equivalence_ctx() -> dict:
    return {}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _registry_tools() -> list[dict]:
    from features.engine.application.tool_catalog import list_tools

    return list_tools() or []


def _mcp_tools() -> list[dict]:
    # NO usar _handle_request: su tools/list consulta _get_tool_catalog()
    # (misma fusion que _registry_tools) — compararlo seria tautologico.
    # Usar el McpServer interno real: es lo que el DISPATCH usa (tools/call)
    # y lo que el CLI (MCPClient) consume. Asi el test detecta drift real
    # entre lo que el agente ve y lo que se puede invocar.
    from features.cli_interface.mcp_adapter import _get_server

    return _get_server().list_tools() or []


# ── Given ────────────────────────────────────────────────────────────────────


@given("el catálogo completo de tools del registry", target_fixture="equivalence_ctx")
def _registry_catalog(equivalence_ctx: dict):
    equivalence_ctx["registry_tools"] = _registry_tools()
    return equivalence_ctx


@given("la respuesta de tools/list del MCP", target_fixture="equivalence_ctx")
def _mcp_catalog(equivalence_ctx: dict):
    equivalence_ctx["mcp_tools"] = _mcp_tools()
    return equivalence_ctx


# ── When ─────────────────────────────────────────────────────────────────────


@when("listo las tools del registry")
def _list_registry(equivalence_ctx: dict):
    equivalence_ctx["registry_tools"] = _registry_tools()


@when("consulto tools/list del MCP")
def _list_mcp(equivalence_ctx: dict):
    equivalence_ctx["mcp_tools"] = _mcp_tools()


# ── Then ─────────────────────────────────────────────────────────────────────


@then("ambos conjuntos de nombres son idénticos")
def _names_identical(equivalence_ctx: dict):
    registry_names = {t.get("name", "") for t in equivalence_ctx["registry_tools"]}
    mcp_names = {t.get("name", "") for t in equivalence_ctx["mcp_tools"]}
    assert registry_names == mcp_names, (
        f"Drift detectado:\n"
        f"  solo registry: {sorted(registry_names - mcp_names)}\n"
        f"  solo MCP:      {sorted(mcp_names - registry_names)}"
    )


@then("no hay tools fantasma en el MCP")
def _no_ghost_tools(equivalence_ctx: dict):
    registry_names = {t.get("name", "") for t in equivalence_ctx["registry_tools"]}
    mcp_names = {t.get("name", "") for t in equivalence_ctx["mcp_tools"]}
    ghosts = mcp_names - registry_names
    assert not ghosts, f"Tools fantasma en MCP (no están en registry): {sorted(ghosts)}"


@then("no hay tools invisibles del registry")
def _no_invisible_tools(equivalence_ctx: dict):
    registry_names = {t.get("name", "") for t in equivalence_ctx["registry_tools"]}
    mcp_names = {t.get("name", "") for t in equivalence_ctx["mcp_tools"]}
    invisible = registry_names - mcp_names
    assert not invisible, f"Tools del registry no expuestas en MCP: {sorted(invisible)}"


@then("cada tool tiene inputSchema tipo object con properties dict")
def _input_schema_valid(equivalence_ctx: dict):
    for t in equivalence_ctx["mcp_tools"]:
        schema = t.get("inputSchema") or {}
        assert schema.get("type") == "object", (
            f"{t.get('name')}: inputSchema sin type object ({schema})"
        )
        assert isinstance(schema.get("properties", {}), dict), (
            f"{t.get('name')}: inputSchema properties no es dict"
        )


@then("cada tool tiene outputSchema dict")
def _output_schema_dict(equivalence_ctx: dict):
    for t in equivalence_ctx["mcp_tools"]:
        assert isinstance(t.get("outputSchema"), dict), (
            f"{t.get('name')}: outputSchema no es dict"
        )


@then("las 6 tools REST del workflow tienen outputSchema con data.properties")
def _rest_tools_output_schema_rich(equivalence_ctx: dict):
    tools = {t.get("name", ""): t for t in equivalence_ctx["mcp_tools"]}
    for name in ("status", "submit", "transition", "prepare", "list", "help"):
        t = tools.get(name)
        assert t is not None, f"{name} no esta en MCP"
        out = t.get("outputSchema") or {}
        data = out.get("properties", {}).get("data", {})
        assert data.get("type") == "object", (
            f"{name}: outputSchema.data no es object ({out})"
        )


@then("kg_task_context está presente")
def _kg_task_context_present(equivalence_ctx: dict):
    names = {t.get("name", "") for t in equivalence_ctx["mcp_tools"]}
    assert "kg_task_context" in names, f"kg_task_context no en MCP: {sorted(names)}"


@then("kg_diagnose está presente")
def _kg_diagnose_present(equivalence_ctx: dict):
    names = {t.get("name", "") for t in equivalence_ctx["mcp_tools"]}
    assert "kg_diagnose" in names, f"kg_diagnose no en MCP: {sorted(names)}"


@then("kg_qa_evidence está presente")
def _kg_qa_evidence_present(equivalence_ctx: dict):
    names = {t.get("name", "") for t in equivalence_ctx["mcp_tools"]}
    assert "kg_qa_evidence" in names, f"kg_qa_evidence no en MCP: {sorted(names)}"


@then("kg_sprint_evidence está presente")
def _kg_sprint_evidence_present(equivalence_ctx: dict):
    names = {t.get("name", "") for t in equivalence_ctx["mcp_tools"]}
    assert "kg_sprint_evidence" in names, f"kg_sprint_evidence no en MCP: {sorted(names)}"
