"""Tests BDD (pytest-bdd) — SSOT del contrato de tareas en el registry.

Feature value: task-manifests eliminado; el contrato vive en la metadata del
task registry y el MCP lo expone como input_schema en discover() y help.

Escenarios: bdd/features/task_contract_ssot.feature
"""
from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/task_contract_ssot.feature")


@pytest.fixture
def ssot_ctx() -> dict:
    return {}


# ── Given ─────────────────────────────────────────────────────────


@given("el task registry bootstrapeado")
def _bootstrap_registry(ssot_ctx: dict):
    from features.main.container import AppContainer

    app = AppContainer.build()
    ssot_ctx["registry"] = app.task_registry


@given("el handler de help construido")
def _help_built(ssot_ctx: dict):
    from features.main.cli_interface.application.handlers import _build_help
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


@then("research_topic expone input_schema")
def _research_topic_schema(ssot_ctx: dict):
    task = ssot_ctx["by_name"].get("research_topic")
    assert task is not None, "research_topic no descubierta"
    assert "input_schema" in task, "research_topic no expone input_schema"


@then("index_repo expone input_schema")
def _index_repo_schema(ssot_ctx: dict):
    task = ssot_ctx["by_name"].get("index_repo")
    assert task is not None, "index_repo no descubierta"
    assert "input_schema" in task, "index_repo no expone input_schema"


@then("los schemas tienen type object")
def _schema_type_object(ssot_ctx: dict):
    for name in ("research_topic", "index_repo"):
        schema = ssot_ctx["by_name"][name]["input_schema"]
        assert schema["type"] == "object", f"{name}: schema type {schema.get('type')} != object"


@then("los schemas tienen clave required")
def _schema_has_required(ssot_ctx: dict):
    for name in ("research_topic", "index_repo"):
        assert "required" in ssot_ctx["by_name"][name]["input_schema"], f"{name} sin required"


@then("url está en required de index_repo")
def _url_in_required(ssot_ctx: dict):
    schema = ssot_ctx["by_name"]["index_repo"]["input_schema"]
    assert "url" in schema["required"], f"url no en required: {schema['required']}"


@then("list_projects no tiene input_schema")
def _list_projects_no_schema(ssot_ctx: dict):
    assert "list_projects" in ssot_ctx["by_name"], "list_projects no descubierta"
    assert "input_schema" not in ssot_ctx["by_name"]["list_projects"], "list_projects no debe tener input_schema"


@then("research_topic aparece en el catálogo")
def _research_topic_in_catalog(ssot_ctx: dict):
    assert "research_topic" in ssot_ctx["by_name"], f"research_topic no en catálogo: {list(ssot_ctx['by_name'].keys())}"


@then("research_topic incluye input_schema")
def _research_topic_includes_schema(ssot_ctx: dict):
    assert "input_schema" in ssot_ctx["by_name"]["research_topic"], "research_topic sin input_schema en help"
