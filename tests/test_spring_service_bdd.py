"""Tests BDD (pytest-bdd) — features/spring service.

Escenarios: tests/features/spring_service.feature
"""
from __future__ import annotations

import json

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/spring_service.feature")


@pytest.fixture
def spring_ctx() -> dict:
    return {}


def _ensure_project(tmp_path):
    cog = tmp_path / ".cognitive" / "projects" / "test-spring"
    cog.mkdir(parents=True)
    (cog / "data").mkdir()
    project_json = {
        "project_id": "test-spring",
        "name": "test-spring",
        "repo_path": str(tmp_path),
        "language": "python",
        "framework": "unknown",
        "status": "active",
    }
    with open(cog / "data" / "project.json", "w") as f:
        json.dump(project_json, f)
    from features.delta_tracking.infrastructure.cognitive_delta_repo import CognitiveDeltaRepository
    CognitiveDeltaRepository(str(cog))
    return str(cog)


@given("un proyecto temporal con .cognitive/", target_fixture="spring_ctx")
def _project_tmp(spring_ctx: dict, tmp_path):
    project_root = _ensure_project(tmp_path)
    spring_ctx["project_root"] = project_root
    return spring_ctx


@when("creo un spring con goal Sprint test", target_fixture="spring_ctx")
def _create_spring(spring_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(spring_ctx["project_root"])
    svc.create_spring("sprint-test", goal="Sprint test", status="planning", project_id="test-spring", session_id="s1")
    spring_ctx["spring_id"] = "sprint-test"
    return spring_ctx


@when("agrego spec con objective Hacer X al spring", target_fixture="spring_ctx")
def _add_spec(spring_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(spring_ctx["project_root"])
    svc.add_spec(spring_ctx["spring_id"], objective="Hacer X", domain="test", spec_type="feature", spec_index=0, status="draft")
    return spring_ctx


@when("el spring tiene 2 specs", target_fixture="spring_ctx")
def _seed_specs(spring_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(spring_ctx["project_root"])
    for i in range(2):
        svc.add_spec(spring_ctx["spring_id"], objective=f"Spec {i}", domain="test", spec_type="feature", spec_index=i, status="draft")
    return spring_ctx


@when("actualizo la spec a in_progress", target_fixture="spring_ctx")
def _update_status(spring_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(spring_ctx["project_root"])
    specs = svc.get_specs(spring_ctx["spring_id"])
    if specs:
        svc.update_spec_status(spring_ctx["spring_id"], specs[0]["spec_id"], "in_progress")
    return spring_ctx


@when("cierro el spring", target_fixture="spring_ctx")
def _close_spring(spring_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(spring_ctx["project_root"])
    svc.set_spring_status(spring_ctx["spring_id"], "closed")
    return spring_ctx


@when("importo un spring que no existe", target_fixture="spring_ctx")
def _import_missing(spring_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(spring_ctx["project_root"])
    result = svc.ensure_spring_imported(spring_ctx["project_root"], "missing-spring")
    spring_ctx["import_result"] = result
    return spring_ctx


@then("el spring existe en el delta")
def _assert_spring_exists(spring_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(spring_ctx["project_root"])
    spring = svc.get_spring(spring_ctx["spring_id"])
    assert spring is not None


@then("el spring.status es planning")
def _assert_spring_status_planning(spring_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(spring_ctx["project_root"])
    spring = svc.get_spring(spring_ctx["spring_id"])
    assert spring is not None
    assert spring.get("status") == "planning"


@then("la spec existe en el delta")
def _assert_spec_exists(spring_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(spring_ctx["project_root"])
    specs = svc.get_specs(spring_ctx["spring_id"])
    assert specs
    spring_ctx["spec"] = specs[0]


@then("la spec.status es draft")
def _assert_spec_status_draft(spring_ctx: dict):
    assert spring_ctx.get("spec", {}).get("status") == "draft"


@then("get_specs devuelve 2 items")
def _assert_spec_count(spring_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(spring_ctx["project_root"])
    specs = svc.get_specs(spring_ctx["spring_id"])
    assert len(specs) == 2


@then("la spec.status es in_progress")
def _assert_spec_status_in_progress(spring_ctx: dict):
    assert spring_ctx.get("spec", {}).get("status") == "in_progress"


@then("el spring.status es closed")
def _assert_spring_status_closed(spring_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(spring_ctx["project_root"])
    spring = svc.get_spring(spring_ctx["spring_id"])
    assert spring is not None
    assert spring.get("status") == "closed"


@then("el resultado es None")
def _assert_none(spring_ctx: dict):
    assert spring_ctx.get("import_result") is None
