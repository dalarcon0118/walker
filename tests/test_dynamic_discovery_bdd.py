"""Tests BDD (pytest-bdd) — Dynamic task discovery y workflow gate sub-state filtering.

Feature value: el health analyzer descubre tasks por capability, no por nombres
hardcodeados; el workflow parsea from_sub/to_sub y filtra gates por sub-estado.

Escenarios: bdd/features/dynamic_discovery.feature
"""
from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/dynamic_discovery.feature")


@pytest.fixture
def dd_ctx() -> dict:
    return {}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_workflow_dict():
    return {
        "version": "3.1",
        "initial_state": "SPRINT",
        "nodes": {
            "SPRINT": {
                "label": "Sprint",
                "sub_states": [
                    {"key": "INIT", "label": "Inicio", "required_fields": []},
                    {
                        "key": "PLANNING",
                        "label": "Planning",
                        "required_fields": ["goal", "specifications"],
                    },
                    {
                        "key": "ACTIVE",
                        "label": "Active",
                        "required_fields": ["goal", "specifications"],
                    },
                    {
                        "key": "REVIEW",
                        "label": "Review",
                        "required_fields": ["decision"],
                    },
                    {"key": "CLOSE", "label": "Close", "required_fields": []},
                ],
            }
        },
        "edges": [
            {
                "from": "SPRINT",
                "from_sub": "INIT",
                "gate": "start_planning",
                "to": "SPRINT",
                "to_sub": "PLANNING",
                "description": "Start sprint planning phase",
            },
            {
                "from": "SPRINT",
                "from_sub": "PLANNING",
                "gate": "start_execution",
                "to": "SPRINT",
                "to_sub": "ACTIVE",
                "description": "Begin sprint execution",
            },
            {
                "from": "SPRINT",
                "from_sub": "PLANNING",
                "gate": "edit_plan",
                "to": "SPRINT",
                "to_sub": "PLANNING",
                "description": "Adjust sprint backlog",
            },
            {
                "from": "SPRINT",
                "from_sub": "ACTIVE",
                "gate": "next_task",
                "to": "SPRINT",
                "to_sub": "ACTIVE",
                "description": "Proceed to next task",
            },
            {
                "from": "SPRINT",
                "from_sub": "ACTIVE",
                "gate": "start_review",
                "to": "SPRINT",
                "to_sub": "REVIEW",
                "description": "Begin sprint review",
            },
            {
                "from": "SPRINT",
                "from_sub": "ACTIVE",
                "gate": "close_sprint",
                "to": "SPRINT",
                "to_sub": "CLOSE",
                "description": "Close sprint directly",
            },
            {
                "from": "SPRINT",
                "from_sub": "REVIEW",
                "gate": "approve",
                "to": "SPRINT",
                "to_sub": "CLOSE",
                "description": "Approve sprint and close",
            },
            {
                "from": "SPRINT",
                "from_sub": "REVIEW",
                "gate": "rework",
                "to": "SPRINT",
                "to_sub": "ACTIVE",
                "description": "Return to active for rework",
            },
        ],
    }


def _build_graph(raw):
    from core.engine.infrastructure.repositories import WorkflowRepository

    return WorkflowRepository()._build_graph(raw)


def _make_state(workflow, sub_state: str, sub_state_index: int, fields=None):
    from core.engine.domain.models import WorkflowState, SubStateData

    sub_state_data = {}
    if fields:
        sub_state_data[sub_state] = SubStateData(fields=fields, completed_at="x")
    return WorkflowState(
        current_node=workflow.initial_state,
        current_sub_state=sub_state,
        sub_state_index=sub_state_index,
        sub_state_data=sub_state_data,
        context={},
    )


# ── Discovery ────────────────────────────────────────────────────────────────


@given("un catálogo con tasks de propósito ckg_populate, cpg_build y sprint_create")
def _catalog_with_purposes(dd_ctx: dict):
    dd_ctx["catalog"] = [
        {"name": "generate_kg", "purpose": ["ckg_populate"]},
        {"name": "rebuild_index", "purpose": ["cpg_build"]},
        {"name": "plan_sprint", "purpose": ["sprint_create"]},
    ]


@given("un health report con warnings de CKG vacío, CPG sin nodos y sprint ausente")
def _health_with_warnings(dd_ctx: dict):
    dd_ctx["health"] = {
        "checks": {"ckg": "empty", "cpg": "0 nodes", "files": "up to date"},
        "warnings": ["CKG is empty"],
        "sprint": None,
        "project": {"language": "ts"},
    }


@given("un health report con warnings de CKG vacío y CPG sin nodos")
def _health_with_ckg_cpg_warnings(dd_ctx: dict):
    dd_ctx["health"] = {
        "checks": {
            "ckg": "empty",
            "cpg": "0 nodes",
            "files": "changed 5 (no new deltas)",
        },
        "warnings": ["CKG is empty"],
        "sprint": None,
        "project": {"language": ""},
    }


@when("se analiza el health report")
def _analyze_health(dd_ctx: dict):
    from features.project import HealthReportAnalyzer

    steps = HealthReportAnalyzer.analyze(
        dd_ctx["health"], "proj", "", task_catalog=dd_ctx.get("catalog", [])
    )
    dd_ctx["commands"] = [s["command"] for s in steps]


@then("se sugieren commands de generate_kg, rebuild_index y plan_sprint")
def _assert_suggested_commands(dd_ctx: dict):
    commands = dd_ctx["commands"]
    assert any("generate_kg" in c for c in commands), commands
    assert any("rebuild_index" in c for c in commands), commands
    assert any("plan_sprint" in c for c in commands), commands


@then("no se sugieren tasks inexistentes como scan_files o build_cpg")
def _assert_no_invalid_tasks(dd_ctx: dict):
    commands = dd_ctx["commands"]
    assert not any("scan_files" in c or "build_cpg" in c for c in commands), commands


@then("no se emite ningún comando task --task")
def _assert_no_invalid_commands(dd_ctx: dict):
    for s in dd_ctx.get("commands", []):
        assert "task --task" not in s, f"comando invalido: {s}"


@given("un catálogo de tasks vacío")
def _empty_catalog(dd_ctx: dict):
    dd_ctx["catalog"] = []


@given("un catálogo con una task renombrada super_scan_v2 de propósito cpg_build")
def _renamed_task_catalog(dd_ctx: dict):
    dd_ctx["catalog"] = [
        {"name": "super_scan_v2", "purpose": ["cpg_build", "code_index"]},
    ]


@given("un health report con CPG vacío")
def _health_cpg_empty(dd_ctx: dict):
    dd_ctx["health"] = {
        "checks": {"ckg": "10 nodes", "cpg": "", "files": "up to date"},
        "warnings": [],
        "sprint": None,
        "project": {"language": "ts"},
    }


@then("se sugiere el command de super_scan_v2")
def _assert_renamed_suggested(dd_ctx: dict):
    commands = dd_ctx["commands"]
    assert any("super_scan_v2" in c for c in commands), commands


# ── Gates ────────────────────────────────────────────────────────────────────


@given("un workflow con edges que especifican from_sub INIT y to_sub PLANNING")
def _workflow_with_sub_states(dd_ctx: dict):
    dd_ctx["raw"] = _make_workflow_dict()
    dd_ctx["graph"] = _build_graph(dd_ctx["raw"])


@when("se construye el grafo del workflow")
def _build_workflow_graph(dd_ctx: dict):
    dd_ctx["graph"] = _build_graph(dd_ctx["raw"])
    by_name = {}
    for gates in dd_ctx["graph"].edges_by_source.values():
        for g in gates:
            by_name[g.name] = g
    dd_ctx["by_name"] = by_name


@then(
    "el gate start_planning tiene source_sub_state INIT y target_sub_state PLANNING"
)
def _assert_start_planning_sub_states(dd_ctx: dict):
    g = dd_ctx["by_name"]["start_planning"]
    assert g.source_sub_state == "INIT"
    assert g.target_sub_state == "PLANNING"


@then(
    "el gate start_execution tiene source_sub_state PLANNING y target_sub_state ACTIVE"
)
def _assert_start_execution_sub_states(dd_ctx: dict):
    g = dd_ctx["by_name"]["start_execution"]
    assert g.source_sub_state == "PLANNING"
    assert g.target_sub_state == "ACTIVE"


@given("un workflow con sub-estados INIT, PLANNING, ACTIVE, REVIEW y CLOSE")
def _workflow_with_all_substates(dd_ctx: dict):
    dd_ctx["workflow"] = _build_graph(_make_workflow_dict())


@given("un workflow con sub-estados que requieren goal y specifications")
def _workflow_requires_goal_specs(dd_ctx: dict):
    dd_ctx["workflow"] = _build_graph(_make_workflow_dict())


def _names_for(dd_ctx, sub, idx, fields=None):
    from core.engine.domain.services import ValidationService

    return [
        g.name
        for g in ValidationService.get_available_gates(
            _make_state(dd_ctx["workflow"], sub, idx, fields), dd_ctx["workflow"]
        )
    ]


@when("se consultan los gates disponibles desde INIT sin datos")
def _gates_from_init(dd_ctx: dict):
    dd_ctx["init_gates"] = _names_for(dd_ctx, "INIT", 0)
    dd_ctx["planning_gates"] = _names_for(dd_ctx, "PLANNING", 1)
    dd_ctx["review_gates"] = _names_for(dd_ctx, "REVIEW", 3)


@then("solo start_planning está disponible")
def _assert_only_start_planning(dd_ctx: dict):
    assert dd_ctx["init_gates"] == ["start_planning"]


@then("desde PLANNING sin datos no hay gates disponibles")
def _assert_planning_empty(dd_ctx: dict):
    assert dd_ctx["planning_gates"] == []


@then("desde REVIEW sin datos no hay gates disponibles")
def _assert_review_empty(dd_ctx: dict):
    assert dd_ctx["review_gates"] == []


@given("el estado PLANNING tiene goal y specifications completos")
def _planning_with_data(dd_ctx: dict):
    dd_ctx["goal_specs"] = {"goal": "g", "specifications": "[]"}


@when("se consultan los gates disponibles desde PLANNING")
def _gates_from_planning(dd_ctx: dict):
    goal_specs = dd_ctx.get("goal_specs", {})
    dd_ctx["planning_gates_full"] = _names_for(dd_ctx, "PLANNING", 1, goal_specs)


@then("start_execution y edit_plan están disponibles")
def _assert_planning_gates(dd_ctx: dict):
    assert dd_ctx["planning_gates_full"] == ["start_execution", "edit_plan"]


@when("se consultan los gates disponibles desde ACTIVE")
def _gates_from_active(dd_ctx: dict):
    goal_specs = dd_ctx.get("goal_specs", {})
    dd_ctx["active_gates_full"] = _names_for(dd_ctx, "ACTIVE", 2, goal_specs)


@then("desde ACTIVE están next_task, start_review y close_sprint")
def _assert_active_gates(dd_ctx: dict):
    assert dd_ctx["active_gates_full"] == [
        "next_task",
        "start_review",
        "close_sprint",
    ]


@when("se consultan los gates disponibles desde REVIEW con decision approve")
def _gates_from_review_with_decision(dd_ctx: dict):
    dd_ctx["review_gates_full"] = _names_for(
        dd_ctx, "REVIEW", 3, {"decision": "approve"}
    )


@then("desde REVIEW con decision están approve y rework")
def _assert_review_gates(dd_ctx: dict):
    assert dd_ctx["review_gates_full"] == ["approve", "rework"]


# ── Transition ───────────────────────────────────────────────────────────────


@given("un workflow con gate start_execution válido solo desde PLANNING")
def _workflow_for_transition(dd_ctx: dict):
    dd_ctx["workflow"] = _build_graph(_make_workflow_dict())


@given("el estado actual en INIT")
def _state_in_init(dd_ctx: dict):
    dd_ctx["state"] = _make_state(dd_ctx["workflow"], "INIT", 0)


@when("se busca el gate start_execution desde INIT")
def _find_gate_from_init(dd_ctx: dict):
    from core.engine.application.use_cases import TransitionUseCase

    dd_ctx["gate"] = TransitionUseCase._find_gate(
        dd_ctx["workflow"], dd_ctx["state"].current_node, "start_execution", "INIT"
    )


@then("no se encuentra el gate")
def _assert_gate_not_found(dd_ctx: dict):
    assert dd_ctx["gate"] is None, "start_execution no debería ser válido desde INIT"


@when("se busca el gate start_planning desde INIT")
def _find_valid_gate(dd_ctx: dict):
    from core.engine.application.use_cases import TransitionUseCase

    dd_ctx["gate_ok"] = TransitionUseCase._find_gate(
        dd_ctx["workflow"], dd_ctx["state"].current_node, "start_planning", "INIT"
    )


@then("el gate start_planning desde INIT sí se encuentra")
def _assert_gate_found(dd_ctx: dict):
    assert dd_ctx["gate_ok"] is not None


@given("un workflow donde REVIEW requiere decision")
def _workflow_review_requires_decision(dd_ctx: dict):
    dd_ctx["workflow"] = _build_graph(_make_workflow_dict())


@given("el estado REVIEW sin decision")
def _review_without_decision(dd_ctx: dict):
    dd_ctx["state_review_empty"] = _make_state(dd_ctx["workflow"], "REVIEW", 3)


@when("se verifican precondiciones para approve")
def _check_preconditions(dd_ctx: dict):
    from core.engine.domain.services import ValidationService

    dd_ctx["errs"] = ValidationService.check_transition_preconditions(
        dd_ctx["state_review_empty"], dd_ctx["workflow"], "approve"
    )


@then("hay errores de precondiciones para REVIEW sin decision")
def _assert_precondition_errors(dd_ctx: dict):
    errs = dd_ctx["errs"]
    assert any("REVIEW" in e and "decision" in e for e in errs), errs


@when("el estado cambia a REVIEW con decision pero fases previas vacías")
def _review_with_decision_empty_prev(dd_ctx: dict):
    from core.engine.domain.models import WorkflowState, SubStateData

    dd_ctx["state_review_partial"] = WorkflowState(
        current_node=dd_ctx["workflow"].initial_state,
        current_sub_state="REVIEW",
        sub_state_index=3,
        sub_state_data={"REVIEW": SubStateData(fields={"decision": "approve"}, completed_at="x")},
        context={},
    )


@when("se re-verifican precondiciones para approve")
def _check_preconditions_partial(dd_ctx: dict):
    from core.engine.domain.services import ValidationService

    dd_ctx["errs_partial"] = ValidationService.check_transition_preconditions(
        dd_ctx["state_review_partial"], dd_ctx["workflow"], "approve"
    )


@then("con decision pero fases previas vacías sigue fallando")
def _assert_partial_still_fails(dd_ctx: dict):
    assert any("PLANNING" in e for e in dd_ctx["errs_partial"]), dd_ctx["errs_partial"]


@when("el estado cambia a REVIEW con todas las fases previas completas")
def _all_phases_complete(dd_ctx: dict):
    from core.engine.domain.models import WorkflowState, SubStateData

    goal_specs = {"goal": "g", "specifications": "[]"}
    dd_ctx["state_complete"] = WorkflowState(
        current_node=dd_ctx["workflow"].initial_state,
        current_sub_state="REVIEW",
        sub_state_index=3,
        sub_state_data={
            "PLANNING": SubStateData(fields=goal_specs, completed_at="x"),
            "ACTIVE": SubStateData(fields=goal_specs, completed_at="x"),
            "REVIEW": SubStateData(fields={"decision": "approve"}, completed_at="x"),
        },
        context={},
    )


@when("se verifican precondiciones finales para approve")
def _check_preconditions_final(dd_ctx: dict):
    from core.engine.domain.services import ValidationService

    dd_ctx["errs_ok"] = ValidationService.check_transition_preconditions(
        dd_ctx["state_complete"], dd_ctx["workflow"], "approve"
    )


@then("con todas las fases previas y REVIEW completas no hay errores")
def _assert_no_errors(dd_ctx: dict):
    assert dd_ctx["errs_ok"] == [], dd_ctx["errs_ok"]
