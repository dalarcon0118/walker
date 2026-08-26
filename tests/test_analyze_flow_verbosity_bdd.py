"""Tests BDD (pytest-bdd) — Reduccion de verbosidad en el flujo ANALYZE.

Feature value: KNOWLEDGE_REVIEW fue eliminado; REVIEW ahora captura
knowledge_items en un solo paso, reduciendo los user_input del PO de 7 a 5.

Escenarios: bdd/features/analyze_flow_verbosity.feature
"""
from __future__ import annotations

import json
import os

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/analyze_flow_verbosity.feature")

NODE_PATH = os.path.join("core", "engine", "workflow", "nodes", "analyze.node.json")


@pytest.fixture
def analyze_ctx() -> dict:
    return {}


def _load(path):
    with open(path) as f:
        return json.load(f)


@given("que el nodo ANALYZE esta definido en core/engine/workflow/nodes/analyze.node.json")
def _load_analyze_node(analyze_ctx: dict):
    analyze_ctx["node"] = _load(NODE_PATH)


@when("se cargan los sub-estados del nodo ANALYZE")
def _extract_keys(analyze_ctx: dict):
    analyze_ctx["keys"] = [s["key"] for s in analyze_ctx["node"]["sub_states"]]


@then("KNOWLEDGE_REVIEW no esta entre las claves de sub-estados")
def _assert_no_knowledge_review(analyze_ctx: dict):
    assert "KNOWLEDGE_REVIEW" not in analyze_ctx["keys"], f"KNOWLEDGE_REVIEW debe eliminarse: {analyze_ctx['keys']}"


@when("se inspecciona el sub-estado REVIEW")
def _inspect_review(analyze_ctx: dict):
    review = next(s for s in analyze_ctx["node"]["sub_states"] if s["key"] == "REVIEW")
    analyze_ctx["review"] = review
    analyze_ctx["props"] = review.get("output_schema", {}).get("properties", {})


@then("su output_schema incluye knowledge_items")
def _assert_review_knowledge_items(analyze_ctx: dict):
    assert "knowledge_items" in analyze_ctx["props"], "REVIEW debe capturar knowledge_items"


@then("su output_schema incluye decision")
def _assert_review_decision(analyze_ctx: dict):
    assert "decision" in analyze_ctx["props"]


@then("su output_schema incluye evidence")
def _assert_review_evidence(analyze_ctx: dict):
    assert "evidence" in analyze_ctx["props"]


@when("se inspecciona el sub-estado KNOWLEDGE_CAPTURE")
def _inspect_kc(analyze_ctx: dict):
    kc = next(s for s in analyze_ctx["node"]["sub_states"] if s["key"] == "KNOWLEDGE_CAPTURE")
    analyze_ctx["kc"] = kc


@then("su on_success es vacio o nulo")
def _assert_kc_fin_nodo(analyze_ctx: dict):
    assert analyze_ctx["kc"].get("on_success") in ("", None), "KNOWLEDGE_CAPTURE debe terminar el nodo"


@given("que existe un spring de session-20260731 cargado")
def _load_spring(analyze_ctx: dict):
    import sys
    sys.path.insert(0, ".")
    from shared.core.engine.infrastructure.repositories import WorkflowRepository
    from shared.core.path_resolver import get_engine_workflow_path
    analyze_ctx["wf"] = WorkflowRepository(workflow_path=str(get_engine_workflow_path())).load()


@when("se ejecuta verify_graph sobre el workflow")
def _verify_graph(analyze_ctx: dict):
    from shared.core.engine.domain.services import ValidationService
    analyze_ctx["v"] = ValidationService.verify_graph(analyze_ctx["wf"])


@then("el grafo es valido")
def _assert_graph_valid(analyze_ctx: dict):
    assert analyze_ctx["v"]["valid"] is True, f"grafo invalido: {analyze_ctx['v']['errors']}"


@then("no hay warnings")
def _assert_no_warnings(analyze_ctx: dict):
    assert len(analyze_ctx["v"]["warnings"]) == 0, f"warnings: {analyze_ctx['v']['warnings']}"


@when("se consulta el mapeo de decision gates de REVIEW")
def _query_review_gate(analyze_ctx: dict):
    from shared.core.engine.application.use_cases import _DECISION_GATE_OVERRIDES
    analyze_ctx["overrides"] = _DECISION_GATE_OVERRIDES.get("REVIEW", {})


@then("approve se resuelve como analysis_approved")
def _assert_review_approve(analyze_ctx: dict):
    assert analyze_ctx["overrides"].get("approve") == "analysis_approved"
