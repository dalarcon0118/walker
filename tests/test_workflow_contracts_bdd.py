"""Tests BDD (pytest-bdd) — DTOs de respuesta de los sub_states llm_task (F3 SPEC-043).

Feature value: cada sub_state llm_task tiene un DTO de respuesta con from_dict()
que valida contra el output_schema del node.json, garantizando anti-alucinación.

Escenarios: tests/features/workflow_contracts.feature
"""
from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/workflow_contracts.feature")


@pytest.fixture
def wf_ctx() -> dict:
    return {}


# ── Helpers ───────────────────────────────────────────────────────


def _all_llm_tasks():
    return [
        ("START", "SELECT"),
        ("PROJECT_INIT", "GATHER_SPECS"),
        ("ANALYZE", "ANALYZE"),
        ("ANALYZE", "KNOWLEDGE_CAPTURE"),
        ("SPRINT_PLANNING", "GOAL"),
        ("SPRINT_PLANNING", "ADD_TASKS"),
        ("SPRINT_PLANNING", "REVIEW"),
        ("DESIGN", "DESIGN_ANALYZE"),
        ("IMPLEMENT", "WORK"),
        ("IMPLEMENT", "CODE_REVIEW_SCAN"),
        ("IMPLEMENT", "KNOWLEDGE_UPDATE"),
        ("REFINE", "DIAGNOSE"),
        ("REFINE", "KNOWLEDGE_CAPTURE"),
        ("SPRINT", "PLANNING"),
        ("SPRINT", "TASK_SPEC"),
        ("SPRINT", "RETRO"),
    ]


# ── Given ─────────────────────────────────────────────────────────


@given("el registro WORKFLOW_OUTPUTS")
def _workflow_outputs(wf_ctx: dict):
    from features.engine.workflow.contracts import WORKFLOW_OUTPUTS
    wf_ctx["WORKFLOW_OUTPUTS"] = WORKFLOW_OUTPUTS


@given("los DTOs TaskSpecOutput, DiagnoseOutput y CodeReviewOutput")
def _dtos_available(wf_ctx: dict):
    from features.engine.workflow.contracts import TaskSpecOutput, DiagnoseOutput, CodeReviewOutput
    wf_ctx["TaskSpecOutput"] = TaskSpecOutput
    wf_ctx["DiagnoseOutput"] = DiagnoseOutput
    wf_ctx["CodeReviewOutput"] = CodeReviewOutput


@given("los DTOs SelectOutput y AddTaskOutput")
def _enum_dtos_available(wf_ctx: dict):
    from features.engine.workflow.contracts import SelectOutput, AddTaskOutput
    wf_ctx["SelectOutput"] = SelectOutput
    wf_ctx["AddTaskOutput"] = AddTaskOutput


@given("el DTO TaskSpecOutput")
def _task_spec_dto(wf_ctx: dict):
    from features.engine.workflow.contracts import TaskSpecOutput
    wf_ctx["TaskSpecOutput"] = TaskSpecOutput


@given("el validador de salida llm_task")
def _validator(wf_ctx: dict):
    from features.engine.workflow.contracts import validate_llm_task_output
    wf_ctx["validate_llm_task_output"] = validate_llm_task_output


@given("entidades SubState con diferentes ejecuciones")
def _substate_entities(wf_ctx: dict):
    from features.engine.domain.entities import SubState
    wf_ctx["llm_sub"] = SubState(key="WORK", execution="llm_task")
    wf_ctx["manual_sub"] = SubState(key="WORK", execution="manual")
    wf_ctx["auto_sub"] = SubState(key="VALIDATE", execution="auto")


# ── When ──────────────────────────────────────────────────────────


@when("verifico cada uno de los 16 sub_states llm_task")
def _verify_16(wf_ctx: dict):
    WORKFLOW_OUTPUTS = wf_ctx["WORKFLOW_OUTPUTS"]
    for node, sub in _all_llm_tasks():
        assert (node, sub) in WORKFLOW_OUTPUTS, f"falta DTO para {node}.{sub}"


@when("creo instancias con datos válidos")
def _create_valid(wf_ctx: dict):
    TaskSpecOutput = wf_ctx["TaskSpecOutput"]
    DiagnoseOutput = wf_ctx["DiagnoseOutput"]
    CodeReviewOutput = wf_ctx["CodeReviewOutput"]
    wf_ctx["task_spec"] = TaskSpecOutput.from_dict({"task_id": "feat-1", "status": "specified"})
    wf_ctx["diagnose"] = DiagnoseOutput.from_dict({"target": "implement", "reason": "root cause x"})
    wf_ctx["code_review"] = CodeReviewOutput.from_dict({"passed": True, "summary": "ok", "findings": []})


@when("creo instancias con datos inválidos")
def _create_invalid(wf_ctx: dict):
    TaskSpecOutput = wf_ctx["TaskSpecOutput"]
    DiagnoseOutput = wf_ctx["DiagnoseOutput"]
    CodeReviewOutput = wf_ctx["CodeReviewOutput"]
    wf_ctx["invalid_results"] = []
    for fn in [
        lambda: TaskSpecOutput.from_dict({"status": "specified"}),
        lambda: CodeReviewOutput.from_dict({"passed": "yes", "summary": "ok"}),
        lambda: DiagnoseOutput.from_dict({"target": "", "reason": "root cause x"}),
    ]:
        try:
            fn()
            wf_ctx["invalid_results"].append(False)
        except Exception:
            wf_ctx["invalid_results"].append(True)


@when("creo instancias con decisión/status inválidos")
def _create_invalid_enum(wf_ctx: dict):
    SelectOutput = wf_ctx["SelectOutput"]
    AddTaskOutput = wf_ctx["AddTaskOutput"]
    wf_ctx["enum_results"] = []
    for fn in [
        lambda: SelectOutput.from_dict({"decision": "deploy"}),
        lambda: AddTaskOutput.from_dict({"status": "deleted"}),
    ]:
        try:
            fn()
            wf_ctx["enum_results"].append(False)
        except Exception:
            wf_ctx["enum_results"].append(True)


@when("genero el schema con to_schema")
def _to_schema(wf_ctx: dict):
    TaskSpecOutput = wf_ctx["TaskSpecOutput"]
    wf_ctx["schema"] = TaskSpecOutput.to_schema()


@when("valido un sub-state sin DTO registrado")
def _validate_no_dto(wf_ctx: dict):
    validate = wf_ctx["validate_llm_task_output"]
    wf_ctx["no_dto_result"] = validate("VIRTUAL", "ANY", {"a": 1})


@when("valido SPRINT.TASK_SPEC sin task_id")
def _validate_missing_field(wf_ctx: dict):
    validate = wf_ctx["validate_llm_task_output"]
    try:
        validate("SPRINT", "TASK_SPEC", {"status": "x"})
        wf_ctx["validation_error"] = None
    except Exception as e:
        wf_ctx["validation_error"] = e


@when("consulto is_llm_task")
def _check_is_llm(wf_ctx: dict):
    wf_ctx["llm_result"] = wf_ctx["llm_sub"].is_llm_task()
    wf_ctx["manual_result"] = wf_ctx["manual_sub"].is_llm_task()
    wf_ctx["auto_result"] = wf_ctx["auto_sub"].is_llm_task()


# ── Then ──────────────────────────────────────────────────────────


@then("cada par (node, sub_state) tiene un DTO de respuesta")
def _all_have_dto(wf_ctx: dict):
    WORKFLOW_OUTPUTS = wf_ctx["WORKFLOW_OUTPUTS"]
    for node, sub in _all_llm_tasks():
        assert (node, sub) in WORKFLOW_OUTPUTS, f"falta DTO para {node}.{sub}"


@then("los DTOs se construyen sin error")
def _dtos_valid(wf_ctx: dict):
    assert wf_ctx["task_spec"] is not None
    assert wf_ctx["diagnose"] is not None
    assert wf_ctx["code_review"] is not None


@then("los campos tienen los valores esperados")
def _fields_match(wf_ctx: dict):
    assert wf_ctx["task_spec"].task_id == "feat-1"
    assert wf_ctx["diagnose"].target == "implement"
    assert wf_ctx["code_review"].passed is True


@then("se eleva ContractOutputError por campo faltante")
def _error_missing(wf_ctx: dict):
    assert all(wf_ctx["invalid_results"]), f"Esperaba errores en todos: {wf_ctx['invalid_results']}"


@then("se eleva ContractOutputError por tipo incorrecto")
def _error_type(wf_ctx: dict):
    assert all(wf_ctx["invalid_results"]), f"Esperaba errores en todos: {wf_ctx['invalid_results']}"


@then("se eleva ContractOutputError por valor vacío")
def _error_empty(wf_ctx: dict):
    assert all(wf_ctx["invalid_results"]), f"Esperaba errores en todos: {wf_ctx['invalid_results']}"


@then("se eleva ContractOutputError para valores fuera de enum")
def _enum_errors(wf_ctx: dict):
    assert all(wf_ctx["enum_results"]), f"Esperaba errores en todos: {wf_ctx['enum_results']}"


@then("el schema tiene type object")
def _schema_has_type(wf_ctx: dict):
    assert wf_ctx["schema"]["type"] == "object"


@then("required incluye task_id y status")
def _required_includes(wf_ctx: dict):
    assert wf_ctx["schema"]["required"] == ["task_id", "status"], wf_ctx["schema"]["required"]


@then("properties incluye task_id")
def _properties_includes(wf_ctx: dict):
    assert "task_id" in wf_ctx["schema"]["properties"], wf_ctx["schema"]["properties"]


@then("retorna la salida sin bloquear")
def _pass_through(wf_ctx: dict):
    assert wf_ctx["no_dto_result"] == {"a": 1}


@then("eleva un error de contrato")
def _contract_error(wf_ctx: dict):
    assert wf_ctx["validation_error"] is not None


@then("llm_task retorna True")
def _llm_true(wf_ctx: dict):
    assert wf_ctx["llm_result"] is True


@then("manual retorna False")
def _manual_false(wf_ctx: dict):
    assert wf_ctx["manual_result"] is False


@then("auto retorna False")
def _auto_false(wf_ctx: dict):
    assert wf_ctx["auto_result"] is False
