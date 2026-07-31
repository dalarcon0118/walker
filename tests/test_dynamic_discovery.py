"""
Test: Dynamic task discovery (health/status) + workflow gate sub-state filtering.

Validates:
  1. HealthReportAnalyzer resolves tasks by capability (purpose), NOT hardcoded names.
  2. No invalid task commands when catalog is empty or task missing.
  3. Gate parser reads from_sub / to_sub from workflow edges.
  4. get_available_gates filters gates by current sub-state.
  5. Transition rejects gates not valid from the current sub-state.
"""

import os
import tempfile


# ════════════════════════════════════════════════════════════════
# 1. Dynamic task discovery in health analyzer
# ════════════════════════════════════════════════════════════════

def test_analyzer_resolves_task_by_purpose():
    """El health analyzer debe descubrir tasks por capacidad, no hardcodear nombres."""
    from features.project.application.use_cases import HealthReportAnalyzer

    catalog = [
        {"name": "generate_kg", "purpose": ["ckg_populate"]},
        {"name": "rebuild_index", "purpose": ["cpg_build"]},
        {"name": "plan_sprint", "purpose": ["sprint_create"]},
    ]
    health = {
        "checks": {"ckg": "empty", "cpg": "0 nodes", "files": "up to date"},
        "warnings": ["CKG is empty"],
        "sprint": None,
        "project": {"language": "ts"},
    }
    steps = HealthReportAnalyzer.analyze(health, "proj", "", task_catalog=catalog)
    commands = [s["command"] for s in steps]
    assert any("generate_kg" in c for c in commands), commands
    assert any("rebuild_index" in c for c in commands), commands
    assert any("plan_sprint" in c for c in commands), commands
    # Nunca debe sugerir una task inexistente
    assert not any("scan_files" in c or "build_cpg" in c for c in commands), commands


def test_analyzer_no_invalid_commands_when_catalog_empty():
    """Con catalogo vacio no debe emitir comandos de task inexistentes."""
    from features.project.application.use_cases import HealthReportAnalyzer

    health = {
        "checks": {"ckg": "empty", "cpg": "0 nodes", "files": "changed 5 (no new deltas)"},
        "warnings": ["CKG is empty"],
        "sprint": None,
        "project": {"language": ""},
    }
    steps = HealthReportAnalyzer.analyze(health, "proj", "", task_catalog=[])
    for s in steps:
        assert "task --task" not in s["command"], f"comando invalido: {s['command']}"


def test_analyzer_renamed_task_still_resolved():
    """Si una task se renombra, el analyzer la descubre por su purpose."""
    from features.project.application.use_cases import HealthReportAnalyzer

    catalog = [
        {"name": "super_scan_v2", "purpose": ["cpg_build", "code_index"]},
    ]
    health = {
        "checks": {"ckg": "10 nodes", "cpg": "", "files": "up to date"},
        "warnings": [],
        "sprint": None,
        "project": {"language": "ts"},
    }
    steps = HealthReportAnalyzer.analyze(health, "proj", "", task_catalog=catalog)
    commands = [s["command"] for s in steps]
    assert any("super_scan_v2" in c for c in commands), commands


# ════════════════════════════════════════════════════════════════
# 2. Gate parsing: from_sub / to_sub
# ════════════════════════════════════════════════════════════════

def _test_workflow_dict():
    """Workflow de prueba inline (NO del motor): nodos + edges con from_sub/to_sub.

    Representa la semántica real de workflow.v3: edges con source_sub_state
    (from_sub) y target (to_sub / sub_state_gate).
    """
    return {
        "version": "3.1",
        "initial_state": "SPRINT",
        "nodes": {
            "SPRINT": {
                "label": "Sprint",
                "sub_states": [
                    {"key": "INIT", "label": "Inicio", "required_fields": []},
                    {"key": "PLANNING", "label": "Planning", "required_fields": ["goal", "specifications"]},
                    {"key": "ACTIVE", "label": "Active", "required_fields": ["goal", "specifications"]},
                    {"key": "REVIEW", "label": "Review", "required_fields": ["decision"]},
                    {"key": "CLOSE", "label": "Close", "required_fields": []},
                ],
            }
        },
        "edges": [
            {"from": "SPRINT", "from_sub": "INIT", "gate": "start_planning", "to": "SPRINT", "to_sub": "PLANNING", "description": "Start sprint planning phase"},
            {"from": "SPRINT", "from_sub": "PLANNING", "gate": "start_execution", "to": "SPRINT", "to_sub": "ACTIVE", "description": "Begin sprint execution"},
            {"from": "SPRINT", "from_sub": "PLANNING", "gate": "edit_plan", "to": "SPRINT", "to_sub": "PLANNING", "description": "Adjust sprint backlog"},
            {"from": "SPRINT", "from_sub": "ACTIVE", "gate": "next_task", "to": "SPRINT", "to_sub": "ACTIVE", "description": "Proceed to next task"},
            {"from": "SPRINT", "from_sub": "ACTIVE", "gate": "start_review", "to": "SPRINT", "to_sub": "REVIEW", "description": "Begin sprint review"},
            {"from": "SPRINT", "from_sub": "ACTIVE", "gate": "close_sprint", "to": "SPRINT", "to_sub": "CLOSE", "description": "Close sprint directly"},
            {"from": "SPRINT", "from_sub": "REVIEW", "gate": "approve", "to": "SPRINT", "to_sub": "CLOSE", "description": "Approve sprint and close"},
            {"from": "SPRINT", "from_sub": "REVIEW", "gate": "rework", "to": "SPRINT", "to_sub": "ACTIVE", "description": "Return to active for rework"},
        ],
    }


def _graph_from_dict(raw: dict):
    from features.engine.infrastructure.repositories import WorkflowRepository

    return WorkflowRepository()._build_graph(raw)


def test_gate_parses_from_sub_and_to_sub():
    """Las edges con from_sub/to_sub deben parsear source/target sub-state."""
    graph = _graph_from_dict(_test_workflow_dict())
    assert graph.nodes, "workflow de prueba no cargo nodos"
    by_name = {}
    for gates in graph.edges_by_source.values():
        for g in gates:
            by_name[g.name] = g
    assert by_name["start_planning"].source_sub_state == "INIT"
    assert by_name["start_planning"].target_sub_state == "PLANNING"
    assert by_name["start_execution"].source_sub_state == "PLANNING"
    assert by_name["start_execution"].target_sub_state == "ACTIVE"
    assert by_name["next_task"].source_sub_state == "ACTIVE"


def _load_emergency_graph():
    return _graph_from_dict(_test_workflow_dict())


# ════════════════════════════════════════════════════════════════
# 3. Available gates filtered by sub-state
# ════════════════════════════════════════════════════════════════

def _make_state(workflow, sub_state: str, sub_state_index: int,
                fields: dict | None = None):
    from features.engine.domain.models import WorkflowState, SubStateData

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


def test_available_gates_filtered_by_sub_state():
    """get_available_gates solo retorna gates validas desde el sub-estado actual."""
    from features.engine.domain.services import ValidationService

    workflow = _load_emergency_graph()
    names_for = lambda sub, idx, fields=None: [
        g.name for g in ValidationService.get_available_gates(
            _make_state(workflow, sub, idx, fields), workflow)
    ]
    # INIT no exige datos → start_planning disponible
    assert names_for("INIT", 0) == ["start_planning"]
    # PLANNING/ACTIVE/REVIEW exigen required_fields: sin datos NO hay gates
    assert names_for("PLANNING", 1) == []
    assert names_for("ACTIVE", 2) == []
    assert names_for("REVIEW", 3) == []
    # Con datos completos, las gates aparecen
    goal_specs = {"goal": "g", "specifications": "[]"}
    assert names_for("PLANNING", 1, goal_specs) == ["start_execution", "edit_plan"]
    assert names_for("ACTIVE", 2, goal_specs) == ["next_task", "start_review", "close_sprint"]
    assert names_for("REVIEW", 3, {"decision": "approve"}) == ["approve", "rework"]


def _load_emergency_graph():
    return _graph_from_dict(_test_workflow_dict())


# ════════════════════════════════════════════════════════════════
# 4. Transition rejects invalid gate from current sub-state
# ════════════════════════════════════════════════════════════════

def test_transition_rejects_gate_from_wrong_sub_state():
    """start_execution no debe ser valido desde INIT (solo desde PLANNING)."""
    from features.engine.application.use_cases import TransitionUseCase

    workflow = _load_emergency_graph()
    state = _make_state(workflow, "INIT", 0)

    # _find_gate (static) rechaza por sub-estado
    gate = TransitionUseCase._find_gate(workflow, state.current_node, "start_execution", "INIT")
    assert gate is None, "start_execution no deberia ser valido desde INIT"

    # Y el gate valido si se encuentra
    gate_ok = TransitionUseCase._find_gate(workflow, state.current_node, "start_planning", "INIT")
    assert gate_ok is not None


def test_transition_preconditions_require_current_sub_state_data():
    """No se puede salir de un sub-estado sin completar sus required_fields."""
    from features.engine.domain.services import ValidationService

    workflow = _load_emergency_graph()

    # REVIEW sin decision → precondiciones fallan (no se omite el actual)
    state_review_empty = _make_state(workflow, "REVIEW", 3)
    errs = ValidationService.check_transition_preconditions(
        state_review_empty, workflow, "approve")
    assert any("REVIEW" in e and "decision" in e for e in errs), errs

    # REVIEW con decision pero fases previas vacias → sigue fallando
    state_review_partial = _make_state(workflow, "REVIEW", 3, {"decision": "approve"})
    errs_partial = ValidationService.check_transition_preconditions(
        state_review_partial, workflow, "approve")
    assert any("PLANNING" in e for e in errs_partial), errs_partial

    # Todas las fases previas + REVIEW completas → ok
    goal_specs = {"goal": "g", "specifications": "[]"}
    from features.engine.domain.models import WorkflowState, SubStateData
    state = WorkflowState(
        current_node=workflow.initial_state,
        current_sub_state="REVIEW",
        sub_state_index=3,
        sub_state_data={
            "PLANNING": SubStateData(fields=goal_specs, completed_at="x"),
            "ACTIVE": SubStateData(fields=goal_specs, completed_at="x"),
            "REVIEW": SubStateData(fields={"decision": "approve"}, completed_at="x"),
        },
        context={},
    )
    errs_ok = ValidationService.check_transition_preconditions(state, workflow, "approve")
    assert errs_ok == [], errs_ok

    # ACTIVE sin goal/specifications → falla
    state_active_empty = _make_state(workflow, "ACTIVE", 2)
    errs_active = ValidationService.check_transition_preconditions(
        state_active_empty, workflow, "start_review")
    assert any("ACTIVE" in e for e in errs_active), errs_active
