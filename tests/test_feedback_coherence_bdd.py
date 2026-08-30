"""Tests BDD (pytest-bdd) — Coherencia del feedback del workflow.

Feature value: los errores del workflow incluyen contexto completo para que el
agente sepa exactamente qué falló y cómo corregirlo.

Escenarios: bdd/features/feedback_coherence.feature
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/feedback_coherence.feature")


@pytest.fixture
def fb_ctx() -> dict:
    return {}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_fake_ctx():
    return type("FakeCtx", (), {"chain_dir": "", "task_id": "", "session_id": ""})()


# ── ErrorHandler ─────────────────────────────────────────────────────────────


@given("un TransitionBlockedError con details de precondiciones")
def _transition_blocked_error_with_details(fb_ctx: dict):
    from shared.core.engine.domain.errors import TransitionBlockedError

    fb_ctx["error"] = TransitionBlockedError(
        "Precondiciones no cumplidas para gate 'next_task'",
        details=[
            "SPEC-001: result_state is empty",
            "SPEC-001: task_description is empty",
            "SPEC-002: status is 'draft' must be 'specified'",
        ],
    )


@when("el ErrorHandler maneja el error")
def _handle_error(fb_ctx: dict):
    from shared.core.engine.application.error_handler import ErrorHandler

    handler = ErrorHandler(
        submit_uc=None,
        transition_uc=None,
        actions=None,
        workflow_store=None,
    )
    fb_ctx["result"] = handler.handle(
        ctx=_make_fake_ctx(),
        error=fb_ctx["error"],
        sub_state=None,
        fields={},
    )


@then("el resultado tiene ok False y strategy block+message")
def _assert_blocked_result(fb_ctx: dict):
    result = fb_ctx["result"]
    assert result["ok"] is False
    assert result["strategy"] == "block+message"


@then("el mensaje incluye los detalles específicos")
def _assert_message_has_details(fb_ctx: dict):
    result = fb_ctx["result"]
    assert "SPEC-001: result_state is empty" in result["message"]
    assert "SPEC-001: task_description is empty" in result["message"]


@then("los detalles están en el resultado")
def _assert_details_in_result(fb_ctx: dict):
    result = fb_ctx["result"]
    assert "details" in result
    assert result["details"]["details"] == fb_ctx["error"].context["details"]


@given("un ValidationError con errors de campos")
def _validation_error_with_errors(fb_ctx: dict):
    from shared.core.engine.domain.errors import ValidationError

    fb_ctx["error"] = ValidationError(
        "Datos inválidos para sub-estado TASK_SPEC",
        errors=[
            {"field": "result_state", "issue": "empty"},
            {"field": "task_description", "issue": "empty"},
        ],
    )


@then("el mensaje incluye los nombres de campos con error")
def _assert_message_has_field_names(fb_ctx: dict):
    result = fb_ctx["result"]
    assert "result_state" in result["message"]
    assert "task_description" in result["message"]


@then("los errors están en los detalles")
def _assert_errors_in_details(fb_ctx: dict):
    result = fb_ctx["result"]
    assert "errors" in result["details"]


@given("un GateValidationError con stderr")
def _gate_validation_error_with_stderr(fb_ctx: dict):
    from shared.core.engine.domain.errors import GateValidationError

    fb_ctx["error"] = GateValidationError(
        "task_complete", "exit code 1: missing required field 'decision'"
    )


@then("el mensaje incluye el stderr")
def _assert_message_has_stderr(fb_ctx: dict):
    result = fb_ctx["result"]
    assert "missing required field" in result["message"]


@then("stderr está en los detalles")
def _assert_stderr_in_details(fb_ctx: dict):
    result = fb_ctx["result"]
    assert "stderr" in result["details"]


@given("un ValidationError genérico sin contexto")
def _generic_validation_error(fb_ctx: dict):
    from shared.core.engine.domain.errors import ValidationError

    fb_ctx["error"] = ValidationError("Algo salio mal")


@then('el mensaje es exactamente "Algo salió mal"')
def _assert_exact_message(fb_ctx: dict):
    result = fb_ctx["result"]
    assert result["message"] == "Algo salio mal"


@then("los detalles están presentes como dict")
def _assert_details_present(fb_ctx: dict):
    result = fb_ctx["result"]
    assert "details" in result
    assert isinstance(result["details"], dict)


# ── validate_plan ────────────────────────────────────────────────────────────


@given("un ExecutionContext y un delta con specs en draft y vacías")
def _exec_context_with_invalid_specs(fb_ctx: dict):
    from shared.core.engine.domain.models import ExecutionContext

    fb_ctx["ctx"] = ExecutionContext.create(
        session_id="test",
        chain_dir="/tmp/fake",
    )
    mock_svc = MagicMock()
    mock_svc.get_spring_id.return_value = "test-sprint"
    mock_svc.get_specs_legacy.return_value = [
        {
            "spec_id": "SPEC-001",
            "status": "draft",
            "result_state": "",
            "task_description": "",
        },
        {
            "spec_id": "SPEC-002",
            "status": "draft",
            "result_state": "",
            "task_description": "",
        },
    ]
    fb_ctx["mock_svc"] = mock_svc
    fb_ctx["patcher"] = patch(
        "features.delta_tracking.application.use_cases.validate_plan._spring_svc",
        return_value=mock_svc,
    )


@when("se ejecuta validate_plan")
def _execute_validate_plan(fb_ctx: dict):
    from tasks.side_effects.validate import validate_plan

    with fb_ctx["patcher"]:
        fb_ctx["result"] = validate_plan(fb_ctx["ctx"], {})


@then("el resultado tiene ok False")
def _assert_result_ok_false(fb_ctx: dict):
    assert fb_ctx["result"]["ok"] is False


@then("tiene summary")
def _assert_has_summary(fb_ctx: dict):
    assert "summary" in fb_ctx["result"]


@then("los errores están en metadata.errors")
def _assert_errors_in_metadata(fb_ctx: dict):
    assert "metadata" in fb_ctx["result"]
    assert fb_ctx["result"]["metadata"]["errors"]


@given("un ExecutionContext y un delta con specs válidas")
def _exec_context_with_valid_specs(fb_ctx: dict):
    from shared.core.engine.domain.models import ExecutionContext

    fb_ctx["ctx"] = ExecutionContext.create(
        session_id="test",
        chain_dir="/tmp/fake",
    )
    mock_svc = MagicMock()
    mock_svc.get_spring_id.return_value = "test-sprint"
    mock_svc.get_specs_legacy.return_value = [
        {
            "spec_id": "SPEC-001",
            "status": "specified",
            "result_state": "implemented",
            "task_description": "Test feature",
            "validation": {"predicates": ["works"]},
            "impacted_artifacts": {"files": ["test.py"]},
            "preconditions": ["none"],
            "acceptance_criteria": ["passes tests"],
            "quality_gates": ["lint"],
            "objective": "Test objective",
        },
    ]
    fb_ctx["mock_svc"] = mock_svc
    fb_ctx["patcher"] = patch(
        "features.delta_tracking.application.use_cases.validate_plan._spring_svc",
        return_value=mock_svc,
    )


@then("metadata.errors está vacío")
def _assert_metadata_errors_empty(fb_ctx: dict):
    assert fb_ctx["result"]["metadata"]["errors"] == []


@then("el resultado tiene ok True")
def _assert_result_ok_true(fb_ctx: dict):
    assert fb_ctx["result"]["ok"] is True


@given("un ExecutionContext y un spring no encontrado")
def _exec_context_spring_not_found(fb_ctx: dict):
    from shared.core.engine.domain.models import ExecutionContext

    fb_ctx["ctx"] = ExecutionContext.create(
        session_id="test",
        chain_dir="/tmp/fake",
    )
    fb_ctx["patcher"] = patch(
        "features.delta_tracking.application.use_cases.validate_plan._spring_svc",
        side_effect=ValueError("spring not found"),
    )


# ── submit_orchestrator ──────────────────────────────────────────────────────


@given("una lista de side-effects con una fallida")
def _side_effects_with_failure(fb_ctx: dict):
    fb_ctx["side_effects"] = [
        {"ok": True, "type": "advance_sprint_task"},
        {
            "ok": False,
            "type": "validate_plan",
            "summary": "validate_plan: 3 error(es) en 2/5 specs",
            "errors": ["SPEC-001: result_state is empty"],
        },
    ]


@when("se filtran las fallidas")
def _filter_failed_side_effects(fb_ctx: dict):
    fb_ctx["failed"] = [
        se
        for se in fb_ctx["side_effects"]
        if isinstance(se, dict) and se.get("ok") is False
    ]


@then("se detecta 1 side-effect fallida")
def _assert_one_failed(fb_ctx: dict):
    assert len(fb_ctx["failed"]) == 1


@then("es de tipo validate_plan")
def _assert_failed_type(fb_ctx: dict):
    assert fb_ctx["failed"][0]["type"] == "validate_plan"


@given("una side-effect fallida con errors")
def _side_effect_with_errors(fb_ctx: dict):
    fb_ctx["side_effect"] = {
        "ok": False,
        "type": "validate_plan",
        "errors": [
            "SPEC-001: result_state is empty",
            "SPEC-001: task_description is empty",
        ],
    }


@when("se accede a errors")
def _access_errors(fb_ctx: dict):
    fb_ctx["errors"] = fb_ctx["side_effect"].get("errors", [])


@then("hay 2 errores")
def _assert_two_errors(fb_ctx: dict):
    assert len(fb_ctx["errors"]) == 2


@then("contienen los mensajes específicos de SPEC-001")
def _assert_specific_messages(fb_ctx: dict):
    assert "SPEC-001: result_state is empty" in fb_ctx["errors"]
    assert "SPEC-001: task_description is empty" in fb_ctx["errors"]


@given("una lista de side-effects todas exitosas")
def _all_successful_side_effects(fb_ctx: dict):
    fb_ctx["side_effects"] = [
        {"ok": True, "type": "advance_sprint_task", "summary": "Task avanzada"},
        {"ok": True, "type": "capture_decision"},
    ]


@then("no hay side-effects fallidas")
def _assert_no_failed(fb_ctx: dict):
    assert len(fb_ctx["failed"]) == 0
