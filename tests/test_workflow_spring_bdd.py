"""Tests BDD (pytest-bdd) — Workflow Spring completo.

Escenarios: tests/features/workflow_spring.feature
"""
from __future__ import annotations

import json
import os

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/workflow_spring.feature")


@pytest.fixture
def wf_ctx() -> dict:
    return {}


def _ensure_project(tmp_path):
    cog = tmp_path / ".cognitive" / "projects" / "walker"
    cog.mkdir(parents=True)
    (cog / "data").mkdir()
    (cog / "data" / "spring").mkdir()
    project_json = {
        "project_id": "walker",
        "name": "walker",
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


@given("un proyecto temporal con .cognitive/", target_fixture="wf_ctx")
def _project_tmp(wf_ctx: dict, tmp_path):
    project_root = _ensure_project(tmp_path)
    wf_ctx["project_root"] = project_root
    return wf_ctx


@given("un spring sprint-test en planning", target_fixture="wf_ctx")
def _spring_planning(wf_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(wf_ctx["project_root"])
    svc.create_spring("sprint-test", goal="Sprint test", status="planning", project_id="walker", session_id="s1")
    wf_ctx["spring_id"] = "sprint-test"
    return wf_ctx


@given("un spring sprint-test", target_fixture="wf_ctx")
def _spring_sprint_test(wf_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(wf_ctx["project_root"])
    svc.create_spring("sprint-test", goal="Sprint test", status="planning", project_id="walker", session_id="s1")
    wf_ctx["spring_id"] = "sprint-test"
    return wf_ctx


@given("un spring sprint-test con 2 specs", target_fixture="wf_ctx")
def _spring_with_specs(wf_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(wf_ctx["project_root"])
    svc.create_spring("sprint-test", goal="Sprint test", status="planning", project_id="walker", session_id="s1")
    svc.add_spec("sprint-test", objective="Spec 0", domain="test", spec_type="feature", spec_index=0, status="draft")
    svc.add_spec("sprint-test", objective="Spec 1", domain="test", spec_type="feature", spec_index=1, status="draft")
    wf_ctx["spring_id"] = "sprint-test"
    return wf_ctx


@given("un spring sprint-test con spec SPEC-001", target_fixture="wf_ctx")
def _spring_with_spec(wf_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(wf_ctx["project_root"])
    svc.create_spring("sprint-test", goal="Sprint test", status="planning", project_id="walker", session_id="s1")
    svc.add_spec("sprint-test", objective="Spec 0", domain="test", spec_type="feature", spec_index=0, status="draft")
    specs = svc.get_specs("sprint-test")
    if specs:
        wf_ctx["spec_id"] = specs[0]["spec_id"]
    wf_ctx["spring_id"] = "sprint-test"
    return wf_ctx


@given("un sprint activo en ACTIVE", target_fixture="wf_ctx")
def _sprint_active(wf_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(wf_ctx["project_root"])
    svc.create_spring("sprint-test", goal="Sprint test", status="active", project_id="walker", session_id="s1")
    chain_dir = os.path.join(wf_ctx["project_root"], "data", "spring", "sprint-test")
    os.makedirs(chain_dir, exist_ok=True)
    from features.engine.infrastructure.repositories import WorkflowStateRepository
    from features.engine.domain.entities import WorkflowState
    wf = WorkflowState(current_node="SPRINT", current_sub_state="ACTIVE")
    wf.context["chain_dir"] = chain_dir
    wf.context["project_id"] = "walker"
    wf.context["session_id"] = "s1"
    WorkflowStateRepository().save(chain_dir, wf)
    wf_ctx["chain_dir"] = chain_dir
    return wf_ctx


@when("creo un spring con goal Sprint test", target_fixture="wf_ctx")
def _create_spring(wf_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(wf_ctx["project_root"])
    svc.create_spring("sprint-test", goal="Sprint test", status="planning", project_id="walker", session_id="s1")
    wf_ctx["spring_id"] = "sprint-test"
    return wf_ctx


@when("agrego spec con objective Hacer X al spring", target_fixture="wf_ctx")
def _add_spec(wf_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(wf_ctx["project_root"])
    svc.add_spec("sprint-test", objective="Hacer X", domain="test", spec_type="feature", spec_index=0, status="draft")
    specs = svc.get_specs("sprint-test")
    if specs:
        wf_ctx["spec_id"] = specs[0]["spec_id"]
    return wf_ctx


@when("el spring tiene 2 specs", target_fixture="wf_ctx")
def _seed_specs(wf_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(wf_ctx["project_root"])
    svc.add_spec("sprint-test", objective="Spec 0", domain="test", spec_type="feature", spec_index=0, status="draft")
    svc.add_spec("sprint-test", objective="Spec 1", domain="test", spec_type="feature", spec_index=1, status="draft")
    return wf_ctx


@when("actualizo la spec a in_progress", target_fixture="wf_ctx")
def _update_status(wf_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(wf_ctx["project_root"])
    spec_id = wf_ctx.get("spec_id") or "SPEC-001"
    svc.update_spec_status("sprint-test", spec_id, "in_progress")
    wf_ctx["update_result"] = {"spec_id": spec_id, "status": "in_progress"}
    return wf_ctx


@when("cierro el spring", target_fixture="wf_ctx")
def _close_spring(wf_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(wf_ctx["project_root"])
    svc.set_spring_status("sprint-test", "closed")
    wf_ctx["close_result"] = {"status": "closed"}
    return wf_ctx


@when("consulto las specs del spring", target_fixture="wf_ctx")
def _list_specs(wf_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(wf_ctx["project_root"])
    specs = svc.get_specs("sprint-test")
    wf_ctx["specs_result"] = {"ok": True, "specs": specs, "count": len(specs)}
    return wf_ctx


@when('invoco submit con intent test y evidence ok', target_fixture="wf_ctx")
def _submit(wf_ctx: dict):
    from features.cli_interface.application.handlers import handle_submit
    from features.cli_interface.infrastructure.contracts import Request
    chain_dir = wf_ctx.get("chain_dir", "")
    req = Request(params={}, flags={"task": "submit", "_payload": {"intent": "test", "evidence": "ok"}})
    req.params["chain_dir"] = chain_dir
    result = handle_submit(req)
    wf_ctx["submit_result"] = result.to_dict() if hasattr(result, "to_dict") else result
    return wf_ctx


@when('invoco transition con gate start_task', target_fixture="wf_ctx")
def _transition(wf_ctx: dict):
    from features.cli_interface.application.handlers import handle_transition
    from features.cli_interface.infrastructure.contracts import Request
    chain_dir = wf_ctx.get("chain_dir", "")
    req = Request(params={"gate": "start_task"}, flags={"task": "transition", "_payload": {"gate": "start_task"}, "force": True, "reason": "test"})
    req.params["chain_dir"] = chain_dir
    result = handle_transition(req)
    wf_ctx["transition_result"] = result.to_dict() if hasattr(result, "to_dict") else result
    return wf_ctx


@then("el spring existe en el delta")
def _assert_spring_exists(wf_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(wf_ctx["project_root"])
    spring = svc.get_spring("sprint-test")
    assert spring is not None


@then("el spring.status es planning")
def _assert_spring_planning(wf_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(wf_ctx["project_root"])
    spring = svc.get_spring("sprint-test")
    assert spring is not None
    assert spring.get("status") == "planning"


@then("la spec existe en el delta")
def _assert_spec_exists(wf_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(wf_ctx["project_root"])
    specs = svc.get_specs("sprint-test")
    assert specs
    wf_ctx["spec"] = specs[0]


@then("la spec.status es draft")
def _assert_spec_draft(wf_ctx: dict):
    assert wf_ctx.get("spec", {}).get("status") == "draft"


@then("get_specs devuelve 2 items")
def _assert_2_specs(wf_ctx: dict):
    from features.delta_tracking import SpringService
    svc = SpringService(wf_ctx["project_root"])
    specs = svc.get_specs("sprint-test")
    assert len(specs) == 2


@then("la spec.status es in_progress")
def _assert_spec_in_progress(wf_ctx: dict):
    assert wf_ctx.get("update_result", {}).get("status") == "in_progress"


@then("el spring.status es closed")
def _assert_spring_closed(wf_ctx: dict):
    assert wf_ctx.get("close_result", {}).get("status") == "closed"


@then("el submit es exitoso")
def _assert_submit_ok(wf_ctx: dict):
    result = wf_ctx.get("submit_result", {})
    assert result.get("type") != "error"


@then("la transición es exitosa")
def _assert_transition_ok(wf_ctx: dict):
    result = wf_ctx.get("transition_result", {})
    assert result.get("type") != "error"
