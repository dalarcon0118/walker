"""Tests BDD (pytest-bdd) — Derivación de campos requeridos en sub-estados.

Feature value: los required_fields se derivan del JSON Schema, eliminando
el campo legacy required_fields de node.json v3 y garantizando que el
schema sea la única fuente de verdad.

Escenarios: bdd/features/required_field_resolver.feature
"""
from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/required_field_resolver.feature")


@pytest.fixture
def rfr_ctx() -> dict:
    return {}


def _make_sub_state(input_schema=None, output_schema=None, legacy=None):
    from core.engine.domain.models import SubState
    return SubState(
        key="TEST",
        input_schema=input_schema or {},
        output_schema=output_schema or {},
        required_fields=legacy or [],
    )


@given("un sub-estado sin input_schema.required")
def _sub_state_no_input_required(rfr_ctx: dict):
    rfr_ctx["sub_state"] = _make_sub_state(
        input_schema={"type": "object"},
        output_schema={"required": ["captured"]},
    )


@given("el sub-estado tiene output_schema que requiere captured")
def _sub_state_with_output_captured(rfr_ctx: dict):
    rfr_ctx["sub_state"] = _make_sub_state(
        output_schema={"required": ["captured"]},
    )


@given("un sub-estado sin input_schema ni output_schema")
def _sub_state_no_schemas(rfr_ctx: dict):
    rfr_ctx["sub_state"] = _make_sub_state(legacy=["goal", "tasks"])


@given("el sub-estado tiene legacy required_fields con goal y tasks")
def _sub_state_with_legacy(rfr_ctx: dict):
    rfr_ctx["sub_state"] = _make_sub_state(legacy=["goal", "tasks"])


@given("un sub-estado sin input_schema ni output_schema ni legacy")
def _sub_state_empty(rfr_ctx: dict):
    rfr_ctx["sub_state"] = _make_sub_state()


@given("un sub-estado None")
def _sub_state_none(rfr_ctx: dict):
    rfr_ctx["sub_state"] = None


@given("un sub-estado SPRINT.PLANNING con input_schema que requiere goal y tasks")
def _sub_state_planning(rfr_ctx: dict):
    rfr_ctx["sub_state"] = _make_sub_state(
        input_schema={
            "type": "object",
            "required": ["goal", "tasks"],
            "properties": {"goal": {"type": "string"}, "tasks": {"type": "array"}},
        }
    )


@given("un sub-estado ANALYZE.ANALYZE con input_schema que requiere q1, a1, q2, a2, q3 y a3")
def _sub_state_analyze(rfr_ctx: dict):
    rfr_ctx["sub_state"] = _make_sub_state(
        input_schema={
            "type": "object",
            "required": ["q1", "a1", "q2", "a2", "q3", "a3"],
        }
    )


@given("un sub-estado con input_schema que requiere decision")
def _sub_state_with_input(rfr_ctx: dict):
    rfr_ctx["sub_state"] = _make_sub_state(
        input_schema={"type": "object", "required": ["decision"]},
        output_schema={"required": ["result_state"]},
        legacy=["goal"],
    )


@when("se derivan los campos requeridos")
def _derive_fields(rfr_ctx: dict):
    from core.engine.domain.services import RequiredFieldResolver
    rfr_ctx["result"] = RequiredFieldResolver.derive(rfr_ctx["sub_state"])


@when("se resuelven los campos requeridos con resolve_required")
def _resolve_required_fields(rfr_ctx: dict):
    from core.engine.domain.services import RequiredFieldResolver
    rfr_ctx["result"] = RequiredFieldResolver.resolve_required(rfr_ctx["sub_state"])


@when("se derivan los campos requeridos con resolve_required")
def _resolve_required_fields(rfr_ctx: dict):
    from core.engine.domain.services import RequiredFieldResolver
    rfr_ctx["result"] = RequiredFieldResolver.resolve_required(rfr_ctx["sub_state"])


@then("los campos requeridos son decision")
def _assert_decision(rfr_ctx: dict):
    assert rfr_ctx.get("result") == ["decision"], f"esperaba ['decision'], got {rfr_ctx.get('result')}"


@then("los campos requeridos son captured")
def _assert_captured(rfr_ctx: dict):
    assert rfr_ctx.get("result") == ["captured"], f"esperaba ['captured'], got {rfr_ctx.get('result')}"


@then("los campos requeridos son goal y tasks")
def _assert_goal_tasks(rfr_ctx: dict):
    result = rfr_ctx.get("result")
    assert set(result) == {"goal", "tasks"}, f"esperaba {{'goal', 'tasks'}}, got {result}"


@then("los campos requeridos estan vacios")
def _assert_empty(rfr_ctx: dict):
    assert rfr_ctx.get("result") == [], f"esperaba [], got {rfr_ctx.get('result')}"


@then("los campos requeridos incluyen goal")
def _assert_goal(rfr_ctx: dict):
    assert "goal" in rfr_ctx.get("result", []), f"goal no en {rfr_ctx.get('result')}"


@then("los campos requeridos incluyen tasks")
def _assert_tasks(rfr_ctx: dict):
    assert "tasks" in rfr_ctx.get("result", []), f"tasks no en {rfr_ctx.get('result')}"


@then("los campos requeridos incluyen q1, a1, q2, a2, q3 y a3")
def _assert_analyze_fields(rfr_ctx: dict):
    result = rfr_ctx.get("result")
    expected = {"q1", "a1", "q2", "a2", "q3", "a3"}
    assert set(result) == expected, f"esperaba {expected}, got {set(result)}"
