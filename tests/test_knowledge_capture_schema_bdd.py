"""Tests BDD (pytest-bdd) — Schema canónico de captura de conocimiento (SPEC-004).

Feature value: los 4 schemas divergentes de captura de conocimiento
referencian un schema canónico vía $ref, garantizando coherencia.

Escenarios: bdd/features/knowledge_capture_schema.feature
"""
from __future__ import annotations

import json
import os

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/knowledge_capture_schema.feature")


@pytest.fixture
def kcs_ctx() -> dict:
    return {}


# ── Helpers ──────────────────────────────────────────────────────────────────


SCHEMA_PATH = os.path.join(
    "core", "engine", "workflow", "schema", "knowledge-capture.schema.json"
)
WORKFLOW_DIR = os.path.join("core", "engine", "workflow", "nodes")
CANONICO = "../schema/knowledge-capture.schema.json"


def _load(path):
    with open(path) as f:
        return json.load(f)


# ── Canónico ──────────────────────────────────────────────────────────────────


@given("el archivo knowledge-capture.schema.json")
def _canonical_schema_file(kcs_ctx: dict):
    kcs_ctx["schema"] = _load(SCHEMA_PATH)


@when("se carga el schema")
def _load_schema(kcs_ctx: dict):
    pass  # ya cargado


@then("el schema tiene $defs")
def _assert_has_defs(kcs_ctx: dict):
    assert kcs_ctx["schema"].get("$defs"), "debe tener $defs"


@then("knowledge_item está definido en $defs")
def _assert_knowledge_item_in_defs(kcs_ctx: dict):
    assert "knowledge_item" in kcs_ctx["schema"]["$defs"], "debe definir knowledge_item"


@when("se inspecciona knowledge_item")
def _inspect_knowledge_item(kcs_ctx: dict):
    kcs_ctx["item"] = kcs_ctx["schema"]["$defs"]["knowledge_item"]


@then("tiene propiedades type, domain, statement y rationale")
def _assert_item_properties(kcs_ctx: dict):
    props = kcs_ctx["item"].get("properties", {})
    assert set(props.keys()) >= {"type", "domain", "statement", "rationale"}


@then("type está en required")
def _assert_type_required(kcs_ctx: dict):
    assert "type" in kcs_ctx["item"].get("required", [])


# ── Refs ─────────────────────────────────────────────────────────────────────


@given("el nodo analyze.node.json")
def _analyze_node(kcs_ctx: dict):
    kcs_ctx["node"] = _load(os.path.join(WORKFLOW_DIR, "analyze.node.json"))


@when("se busca el sub-estado KNOWLEDGE_CAPTURE")
def _find_knowledge_capture(kcs_ctx: dict):
    kcs_ctx["ss"] = next(
        s for s in kcs_ctx["node"]["sub_states"] if s["key"] == "KNOWLEDGE_CAPTURE"
    )


@then("su output_schema tiene $ref al canónico")
def _assert_output_ref(kcs_ctx: dict):
    assert kcs_ctx["ss"]["output_schema"]["$ref"] == CANONICO


@given("el nodo refine.node.json")
def _refine_node(kcs_ctx: dict):
    kcs_ctx["node"] = _load(os.path.join(WORKFLOW_DIR, "refine.node.json"))


@given("el nodo implement.node.json")
def _implement_node(kcs_ctx: dict):
    kcs_ctx["node"] = _load(os.path.join(WORKFLOW_DIR, "implement.node.json"))


@when("se busca el sub-estado KNOWLEDGE_UPDATE")
def _find_knowledge_update(kcs_ctx: dict):
    kcs_ctx["ss"] = next(
        s for s in kcs_ctx["node"]["sub_states"] if s["key"] == "KNOWLEDGE_UPDATE"
    )


@given("el nodo sprint.node.json")
def _sprint_node(kcs_ctx: dict):
    kcs_ctx["node"] = _load(os.path.join(WORKFLOW_DIR, "sprint.node.json"))


@when("se busca el sub-estado RETRO")
def _find_retro(kcs_ctx: dict):
    kcs_ctx["ss"] = next(
        s for s in kcs_ctx["node"]["sub_states"] if s["key"] == "RETRO"
    )


# ── Carga ─────────────────────────────────────────────────────────────────────


@given("un directorio de spring con schemas que usan $ref")
def _spring_dir_with_refs(kcs_ctx: dict):
    kcs_ctx["chain_dir"] = os.path.join(
        ".cognitive", "projects", "walker", "data", "spring", "session-20260731"
    )


@when("se carga el workflow desde el repositorio")
def _load_workflow(kcs_ctx: dict):
    import sys

    sys.path.insert(0, ".")
    from shared.core.engine.infrastructure.repositories import WorkflowRepository
    from app.path_resolver import get_engine_workflow_path

    kcs_ctx["wf"] = WorkflowRepository(workflow_path=str(get_engine_workflow_path())).load()


@then("el workflow tiene 8 nodos")
def _assert_8_nodes(kcs_ctx: dict):
    assert len(kcs_ctx["wf"].nodes) == 8
