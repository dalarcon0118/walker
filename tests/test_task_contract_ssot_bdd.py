"""Tests BDD (pytest-bdd) — SSOT del contrato de tareas en el registry.

Feature value: task-manifests eliminado; el contrato vive en la metadata del
task registry y el MCP lo expone como input_schema en discover() y help.

Escenarios: tests/features/task_contract_ssot.feature
"""
from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/task_contract_ssot.feature")


@pytest.fixture
def ssot_ctx() -> dict:
    return {}


# ── Given ─────────────────────────────────────────────────────────


@given("el task registry bootstrapeado")
def _bootstrap_registry(ssot_ctx: dict):
    from features.engine.bootstrap import bootstrap
    from features.engine.infrastructure.task_registry import InMemoryTaskRegistry
    from features.engine.infrastructure.trace_log import TraceLogger
    registry, _ = bootstrap(InMemoryTaskRegistry(tracer=TraceLogger()))
    ssot_ctx["registry"] = registry


@given("el handler de help construido")
def _help_built(ssot_ctx: dict):
    from features.cli_interface.application.handlers import _build_help
    ssot_ctx["help_data"] = _build_help()


# ── When ──────────────────────────────────────────────────────────


@when("descubro todas las tasks")
def _discover_all(ssot_ctx: dict):
    ssot_ctx["catalog"] = ssot_ctx["registry"].discover(scope="all")
    ssot_ctx["by_name"] = {t["name"]: t for t in ssot_ctx["catalog"]["tasks"]}


@when("obtengo el catálogo de tareas")
def _get_task_catalog(ssot_ctx: dict):
    catalog = ssot_ctx["help_data"].get("task_catalog", {})
    ssot_ctx["tasks"] = catalog.get("tasks", [])
    ssot_ctx["by_name"] = {t.get("name"): t for t in ssot_ctx["tasks"]}


# ── Then ──────────────────────────────────────────────────────────


@then("kg_research expone input_schema")
def _kg_research_schema(ssot_ctx: dict):
    task = ssot_ctx["by_name"].get("kg_research")
    assert task is not None, "kg_research no descubierta"
    assert "input_schema" in task, "kg_research no expone input_schema"


@then("kg_index_repo expone input_schema")
def _kg_index_repo_schema(ssot_ctx: dict):
    task = ssot_ctx["by_name"].get("kg_index_repo")
    assert task is not None, "kg_index_repo no descubierta"
    assert "input_schema" in task, "kg_index_repo no expone input_schema"


@then("los schemas tienen type object")
def _schema_type_object(ssot_ctx: dict):
    for name in ("kg_research", "kg_index_repo"):
        schema = ssot_ctx["by_name"][name]["input_schema"]
        assert schema["type"] == "object", f"{name}: schema type {schema.get('type')} != object"


@then("los schemas tienen clave required")
def _schema_has_required(ssot_ctx: dict):
    for name in ("kg_research", "kg_index_repo"):
        assert "required" in ssot_ctx["by_name"][name]["input_schema"], f"{name} sin required"


@then("url está en required de kg_index_repo")
def _url_in_required(ssot_ctx: dict):
    schema = ssot_ctx["by_name"]["kg_index_repo"]["input_schema"]
    assert "url" in schema["required"], f"url no en required: {schema['required']}"


@then("list_projects no tiene input_schema")
def _list_projects_no_schema(ssot_ctx: dict):
    assert "list_projects" in ssot_ctx["by_name"], "list_projects no descubierta"
    assert "input_schema" not in ssot_ctx["by_name"]["list_projects"], "list_projects no debe tener input_schema"


@then("kg_research aparece en el catálogo")
def _kg_research_in_catalog(ssot_ctx: dict):
    assert "kg_research" in ssot_ctx["by_name"], f"kg_research no en catálogo: {list(ssot_ctx['by_name'].keys())}"


@then("kg_research incluye input_schema")
def _kg_research_includes_schema(ssot_ctx: dict):
    assert "input_schema" in ssot_ctx["by_name"]["kg_research"], "kg_research sin input_schema en help"
