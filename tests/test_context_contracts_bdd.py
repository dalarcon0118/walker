"""Tests BDD (pytest-bdd) — Contratos de contexto tipados por provider.

Feature value: los 9 providers de contexto exponen schemas JSON coherentes,
el compositor puebla context_schemas desde output_schema o fallback SSOT, y
workflow.json declara los 9 providers.

Escenarios: bdd/features/context_contracts.feature
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/context_contracts.feature")


@pytest.fixture
def ctx_contract_ctx() -> dict:
    return {}


def _all_providers():
    return [
        "business",
        "ckg",
        "cpg",
        "delta",
        "diff",
        "project",
        "reflection",
        "test_results",
        "trace",
    ]


@given("que existen 9 providers de contexto declarados en los registros")
def _setup_providers(ctx_contract_ctx: dict):
    ctx_contract_ctx["providers"] = _all_providers()


@when("se consulta CONTEXT_SCHEMAS")
def _query_context_schemas(ctx_contract_ctx: dict):
    from shared.core.engine.context.contracts import CONTEXT_SCHEMAS
    ctx_contract_ctx["schemas"] = CONTEXT_SCHEMAS


@then("los 9 providers tienen un schema registrado")
def _assert_9_providers(ctx_contract_ctx: dict):
    for provider in ctx_contract_ctx["providers"]:
        assert provider in ctx_contract_ctx["schemas"], f"falta schema para '{provider}'"


@then("cada schema declara type object")
def _assert_all_type_object(ctx_contract_ctx: dict):
    for provider, schema in ctx_contract_ctx["schemas"].items():
        assert schema["type"] == "object", f"{provider}: schema sin type object"


@when("se consulta context_schema para un provider imaginario")
def _query_unknown_provider(ctx_contract_ctx: dict):
    from shared.core.engine.context.contracts import context_schema
    ctx_contract_ctx["unknown"] = context_schema("provider_imaginario")


@when("se consulta context_schema para cadena vacia")
def _query_empty_provider(ctx_contract_ctx: dict):
    from shared.core.engine.context.contracts import context_schema
    ctx_contract_ctx["empty"] = context_schema("")


@then("retorna {}")
def _assert_empty_schema(ctx_contract_ctx: dict):
    assert ctx_contract_ctx["unknown"] == {}


@when("se consulta context_schema para cadena vacia")
def _query_empty_provider(ctx_contract_ctx: dict):
    from shared.core.engine.context.contracts import context_schema
    ctx_contract_ctx["empty"] = context_schema("")


@then("context_schema para cadena vacia retorna {}")
def _assert_empty_string_schema(ctx_contract_ctx: dict):
    assert ctx_contract_ctx["empty"] == {}


@when("se consulta all_context_schemas")
def _query_all_schemas(ctx_contract_ctx: dict):
    from shared.core.engine.context.contracts import all_context_schemas
    ctx_contract_ctx["all"] = all_context_schemas()


@when("se consultan todos los context_schemas")
def _query_all_schemas_alt(ctx_contract_ctx: dict):
    from shared.core.engine.context.contracts import all_context_schemas
    ctx_contract_ctx["all"] = all_context_schemas()


@then("all_context_schemas retorna exactamente los 9 providers")
def _assert_all_9(ctx_contract_ctx: dict):
    assert set(ctx_contract_ctx["all"]) == set(ctx_contract_ctx["providers"])


@when("se inspecciona el schema de cpg")
def _inspect_cpg(ctx_contract_ctx: dict):
    from shared.core.engine.context.contracts import CONTEXT_SCHEMAS
    ctx_contract_ctx["cpg"] = CONTEXT_SCHEMAS["cpg"]


@then("cpg incluye files y symbols")
def _assert_cpg_props(ctx_contract_ctx: dict):
    assert "files" in ctx_contract_ctx["cpg"]["properties"]
    assert "symbols" in ctx_contract_ctx["cpg"]["properties"]


@when("se inspecciona el schema de diff")
def _inspect_diff(ctx_contract_ctx: dict):
    from shared.core.engine.context.contracts import CONTEXT_SCHEMAS
    ctx_contract_ctx["diff"] = CONTEXT_SCHEMAS["diff"]


@then("diff incluye base_ref y files")
def _assert_diff_props(ctx_contract_ctx: dict):
    assert "base_ref" in ctx_contract_ctx["diff"]["properties"]
    assert "files" in ctx_contract_ctx["diff"]["properties"]


@when("se inspecciona el schema de test_results")
def _inspect_test_results(ctx_contract_ctx: dict):
    from shared.core.engine.context.contracts import CONTEXT_SCHEMAS
    ctx_contract_ctx["test_results"] = CONTEXT_SCHEMAS["test_results"]


@then("test_results incluye passed y failed")
def _assert_test_results_props(ctx_contract_ctx: dict):
    assert "passed" in ctx_contract_ctx["test_results"]["properties"]
    assert "failed" in ctx_contract_ctx["test_results"]["properties"]


@when("se inspecciona el schema de trace")
def _inspect_trace(ctx_contract_ctx: dict):
    from shared.core.engine.context.contracts import CONTEXT_SCHEMAS
    ctx_contract_ctx["trace"] = CONTEXT_SCHEMAS["trace"]


@then("trace incluye events y tool")
def _assert_trace_props(ctx_contract_ctx: dict):
    assert "events" in ctx_contract_ctx["trace"]["properties"]
    assert "tool" in ctx_contract_ctx["trace"]["properties"]["events"]["items"]["properties"]


class _SchemaProvider:
    def instructions(self, config, ctx):
        return "Layer: package — test provider."

    def collect(self, config, ctx, query_client=None):
        from shared.core.engine.context.models import ContextPiece
        return ContextPiece(
            provider="package",
            name="package layer",
            content="packages: x, y",
            tokens=10,
            priority=10,
        )

    def output_schema(self):
        return {"type": "object", "properties": {"packages": {"type": "array"}}}


def _minimal_ctx():
    return SimpleNamespace(
        chain_dir="",
        task_id="task-f2-1",
        session_id="",
        baggage={},
    )


@given("un provider stub con output_schema")
def _setup_schema_provider(ctx_contract_ctx: dict):
    ctx_contract_ctx["provider"] = _SchemaProvider()


@when("se compone el contexto")
def _compose_with_provider(ctx_contract_ctx: dict):
    from shared.core.engine.context.composer import ContextComposer
    from shared.core.engine.context.registry import ContextProviderRegistry

    provider = ctx_contract_ctx.get("provider") or ctx_contract_ctx.get("stub_provider")
    registry = ContextProviderRegistry()
    registry.register("package", provider)
    composer = ContextComposer(registry)

    composed = composer.compose(
        {"sources": [{"provider": "package", "config": {}}], "focus": {}},
        "Work here: {task_id}",
        _minimal_ctx(),
    )
    ctx_contract_ctx["composed"] = composed


@then("el ComposedContext incluye el schema del provider")
def _assert_composed_has_schema(ctx_contract_ctx: dict):
    assert "package" in ctx_contract_ctx["composed"].context_schemas


@then("el schema tiene packages tipo array")
def _assert_packages_array(ctx_contract_ctx: dict):
    pkg = ctx_contract_ctx["composed"].context_schemas["package"]
    assert pkg["properties"]["packages"]["type"] == "array"


class _StubProvider:
    def instructions(self, config, ctx):
        return "Layer: diff."

    def collect(self, config, ctx, query_client=None):
        from shared.core.engine.context.models import ContextPiece
        return ContextPiece(
            provider="diff",
            name="diff layer",
            content="files: a.py",
            tokens=5,
            priority=10,
        )


@given("un provider stub sin output_schema")
def _setup_stub_provider(ctx_contract_ctx: dict):
    ctx_contract_ctx["stub_provider"] = _StubProvider()


@when("se compone el contexto para el provider sin output_schema")
def _compose_stub_provider(ctx_contract_ctx: dict):
    from shared.core.engine.context.contracts import CONTEXT_SCHEMAS
    from shared.core.engine.context.composer import ContextComposer
    from shared.core.engine.context.registry import ContextProviderRegistry

    registry = ContextProviderRegistry()
    registry.register("diff", ctx_contract_ctx["stub_provider"])
    composer = ContextComposer(registry)

    composed = composer.compose(
        {"sources": [{"provider": "diff", "config": {}}], "focus": {}},
        "Work here: {task_id}",
        _minimal_ctx(),
    )
    ctx_contract_ctx["stub_composed"] = composed
    ctx_contract_ctx["CONTEXT_SCHEMAS"] = CONTEXT_SCHEMAS


@then("el ComposedContext incluye el schema del provider desde CONTEXT_SCHEMAS")
def _assert_stub_composed_has_schema(ctx_contract_ctx: dict):
    assert "diff" in ctx_contract_ctx["composed"].context_schemas


@then("el schema es el mismo objeto registrado en CONTEXT_SCHEMAS")
def _assert_same_sso_object(ctx_contract_ctx: dict):
    from shared.core.engine.context.contracts import CONTEXT_SCHEMAS
    assert ctx_contract_ctx["composed"].context_schemas["diff"] is CONTEXT_SCHEMAS["diff"]


@when("se carga workflow.json")
def _load_workflow_json(ctx_contract_ctx: dict):
    import json
    import os
    here = os.path.dirname(__file__)
    wf_path = os.path.join(here, "..", "core", "engine", "workflow", "workflow.json")
    with open(wf_path) as f:
        ctx_contract_ctx["workflow"] = json.load(f)


@then("la seccion context_schema.providers incluye los 9 providers")
def _assert_workflow_providers(ctx_contract_ctx: dict):
    providers = ctx_contract_ctx["workflow"]["context_schema"]["providers"]
    for provider in _all_providers():
        assert provider in providers, f"workflow.json sin schema de {provider}"
