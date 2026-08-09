"""Tests BDD (pytest-bdd) — Integridad estructural de los ADRs.

Feature value: los ADRs cumplen un schema JSON estricto con campos requeridos,
ids válidos, statuses válidos, relaciones válidas y estructura correcta de
decision/impact/implementation_steps.

Escenarios: tests/features/adr_schema.feature
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/adr_schema.feature")

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"
ADRS_DIR = SCHEMA_DIR / "adrs"

VALID_STATUS = {"Proposed", "Accepted", "In Progress", "Superseded", "Rejected"}
VALID_RELATIONS = {"complements", "supersedes", "superseded-by", "related", "depends-on"}
REQUIRED_FIELDS = ["id", "status", "date", "summary", "context", "decision", "impact"]


@pytest.fixture
def adr_ctx() -> dict:
    return {}


def _load_adrs() -> list[tuple[str, dict]]:
    if not ADRS_DIR.exists():
        return []
    return [
        (p.name, json.loads(p.read_text()))
        for p in sorted(ADRS_DIR.glob("*.json"))
        if p.suffix == ".json"
    ]


@given("que el directorio schemas/adrs/ contiene ADRs JSON")
def _setup_adrs(adr_ctx: dict):
    adr_ctx["adrs"] = _load_adrs()


@given("que existen ADRs en schemas/adrs/")
def _ensure_adrs_loaded(adr_ctx: dict):
    if "adrs" not in adr_ctx:
        adr_ctx["adrs"] = _load_adrs()


@when("se carga el schema adr-walker.schema.json")
def _load_schema(adr_ctx: dict):
    schema_path = SCHEMA_DIR / "adr-walker.schema.json"
    adr_ctx["schema_path"] = schema_path
    adr_ctx["schema"] = json.loads(schema_path.read_text())


@then("el schema es JSON válido")
def _schema_valid(adr_ctx: dict):
    assert adr_ctx["schema"] is not None


@then("el schema declara type object")
def _schema_type_object(adr_ctx: dict):
    assert adr_ctx["schema"]["type"] == "object"


@then("el schema declara campos required")
def _schema_has_required(adr_ctx: dict):
    assert adr_ctx["schema"].get("required"), "El schema debe declarar campos required"


@when("se valida cada ADR contra los campos requeridos")
def _validate_required_fields(adr_ctx: dict):
    adr_ctx["missing"] = []
    for name, adr in adr_ctx["adrs"]:
        for field in REQUIRED_FIELDS:
            if field not in adr:
                adr_ctx["missing"].append((name, field))


@then("cada ADR tiene id, status, date, summary, context, decision e impact")
def _assert_required_fields(adr_ctx: dict):
    assert adr_ctx["missing"] == [], f"Campos faltantes: {adr_ctx['missing']}"


@when("se valida el formato del id")
def _validate_ids(adr_ctx: dict):
    adr_ctx["invalid_ids"] = []
    adr_ctx["invalid_statuses"] = []
    for name, adr in adr_ctx["adrs"]:
        if not re.match(r"^ADR-WALKER-\d{3}$", adr["id"]):
            adr_ctx["invalid_ids"].append((name, adr["id"]))
        if adr["status"] not in VALID_STATUS:
            adr_ctx["invalid_statuses"].append((name, adr["status"]))


@then("el id cumple el patron ADR-WALKER-\\d{3}")
def _assert_id_pattern(adr_ctx: dict):
    assert adr_ctx["invalid_ids"] == [], f"IDs inválidos: {adr_ctx['invalid_ids']}"


@then("el status pertenece al conjunto de estados válidos")
def _assert_valid_status(adr_ctx: dict):
    assert adr_ctx["invalid_statuses"] == [], f"Status inválidos: {adr_ctx['invalid_statuses']}"


@when("se valida cada relacion related")
def _validate_relations(adr_ctx: dict):
    adr_ctx["relation_errors"] = []
    for name, adr in adr_ctx["adrs"]:
        for rel in adr.get("related", []):
            if "id" not in rel:
                adr_ctx["relation_errors"].append((name, "sin id"))
            relation = rel.get("relation", "complements")
            if relation not in VALID_RELATIONS:
                adr_ctx["relation_errors"].append((name, f"relation inválida: {relation!r}"))


@then("cada relacion tiene id")
def _assert_relation_has_id(adr_ctx: dict):
    errors = [e for e in adr_ctx["relation_errors"] if e[1] == "sin id"]
    assert errors == [], f"Relaciones sin id: {errors}"


@then("cada relation pertenece al conjunto de relaciones válidas")
def _assert_valid_relations(adr_ctx: dict):
    errors = [e for e in adr_ctx["relation_errors"] if e[1] != "sin id"]
    assert errors == [], f"Relaciones inválidas: {errors}"


@when("se valida la estructura de decision e impact")
def _validate_decision_impact(adr_ctx: dict):
    adr_ctx["structure_errors"] = []
    for name, adr in adr_ctx["adrs"]:
        decision = adr.get("decision", {})
        if not decision.get("statement"):
            adr_ctx["structure_errors"].append((name, "decision.statement vacío"))
        impact = adr.get("impact", {})
        if not isinstance(impact.get("changes", []), list):
            adr_ctx["structure_errors"].append((name, "impact.changes no es lista"))


@then("decision.statement no esta vacío")
def _assert_decision_statement(adr_ctx: dict):
    errors = [e for e in adr_ctx["structure_errors"] if "decision.statement" in e[1]]
    assert errors == [], f"decision.statement vacío: {errors}"


@then("impact.changes es una lista")
def _assert_impact_changes_list(adr_ctx: dict):
    errors = [e for e in adr_ctx["structure_errors"] if "impact.changes" in e[1]]
    assert errors == [], f"impact.changes no es lista: {errors}"


@when("se valida cada paso de implementacion")
def _validate_implementation_steps(adr_ctx: dict):
    adr_ctx["step_errors"] = []
    for name, adr in adr_ctx["adrs"]:
        for step in adr.get("implementation_steps", []):
            if not step.get("description"):
                adr_ctx["step_errors"].append((name, "sin description"))
            if "done" in step and not isinstance(step["done"], bool):
                adr_ctx["step_errors"].append((name, "done no es bool"))


@then("cada paso tiene descripcion")
def _assert_step_description(adr_ctx: dict):
    errors = [e for e in adr_ctx["step_errors"] if e[1] == "sin description"]
    assert errors == [], f"Pasos sin descripción: {errors}"


@then("si existe done, es booleano")
def _assert_step_done_bool(adr_ctx: dict):
    errors = [e for e in adr_ctx["step_errors"] if e[1] == "done no es bool"]
    assert errors == [], f"done no es bool: {errors}"


@when("se carga el listado de ADRs")
def _load_adrs_list(adr_ctx: dict):
    adr_ctx["adrs"] = _load_adrs()


@then("se encuentra al menos un ADR JSON")
def _assert_at_least_one(adr_ctx: dict):
    assert len(adr_ctx["adrs"]) >= 1, "No hay ADRs JSON en schemas/adrs/"
