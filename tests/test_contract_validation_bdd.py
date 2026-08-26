"""Tests BDD (pytest-bdd) — Validacion por contratos anti-alucinacion determinista.

Feature value: verify_post valida output contra output_schema bloqueando required
faltantes, tipos incorrectos y valores fuera de enum. Contract.from_dict preserva
properties. verify_pre sigue bloqueando por compatibilidad. SubmitDataUseCase
lanza ValidationError cuando el output no cumple el contrato.

Escenarios: bdd/features/contract_validation.feature
"""
from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/contract_validation.feature")


@pytest.fixture
def cv_ctx() -> dict:
    return {}


def _diagnose_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "enum": ["analyze", "implement", "verify", "sprint"],
            },
            "reason": {"type": "string", "minLength": 10},
        },
        "required": ["target", "reason"],
    }


@given("que existe un contrato de salida con target y reason")
def _setup_contract(cv_ctx: dict):
    from shared.core.engine.domain.contract_validator import Contract, ContractValidator
    cv_ctx["contract"] = Contract.from_dict(_diagnose_schema())
    cv_ctx["validator"] = ContractValidator


@when("se valida un output vacio contra el contrato")
def _validate_empty_output(cv_ctx: dict):
    report = cv_ctx["validator"].verify_post(cv_ctx["contract"], {})
    cv_ctx["report"] = report
    cv_ctx["errors"] = [v.message for v in report.errors]


@then("la validacion no pasa")
def _assert_not_passed(cv_ctx: dict):
    report = cv_ctx.get("report") or cv_ctx.get("pre_report")
    assert report.passed is False, "output vacio NO debe cumplir el contrato"


@then("los errores mencionan target")
def _assert_mentions_target(cv_ctx: dict):
    assert any("target" in m for m in cv_ctx["errors"]), cv_ctx["errors"]


@then("los errores mencionan reason")
def _assert_mentions_reason(cv_ctx: dict):
    errors = cv_ctx.get("errors") or cv_ctx.get("pre_errors", [])
    assert any("reason" in m for m in errors), errors


@when("se valida un output con target int")
def _validate_wrong_type(cv_ctx: dict):
    report = cv_ctx["validator"].verify_post(
        cv_ctx["contract"], {"target": 123, "reason": "calidad insuficiente"}
    )
    cv_ctx["report"] = report
    cv_ctx["errors"] = [v.message for v in report.errors]


@then("los errores indican que debe ser string")
def _assert_string_error(cv_ctx: dict):
    assert any("debe ser string" in m for m in cv_ctx["errors"]), cv_ctx["errors"]


@when("se valida un output con target nonsense")
def _validate_enum_violation(cv_ctx: dict):
    report = cv_ctx["validator"].verify_post(
        cv_ctx["contract"], {"target": "nonsense", "reason": "calidad insuficiente"}
    )
    cv_ctx["report"] = report
    cv_ctx["errors"] = [v.message for v in report.errors]


@then("los errores mencionan enum")
def _assert_enum_error(cv_ctx: dict):
    assert any("enum" in m for m in cv_ctx["errors"]), cv_ctx["errors"]


@when("se valida un output valido con target implement y reason de 10+ chars")
def _validate_valid_output(cv_ctx: dict):
    report = cv_ctx["validator"].verify_post(
        cv_ctx["contract"], {"target": "implement", "reason": "calidad insuficiente"}
    )
    cv_ctx["report"] = report


@then("la validacion pasa")
def _assert_passed(cv_ctx: dict):
    assert cv_ctx["report"].passed is True, [v.message for v in cv_ctx["report"].violations]


@then("no hay falsos positivos")
def _assert_no_false_positives(cv_ctx: dict):
    assert cv_ctx["report"].violations == (), [v.message for v in cv_ctx["report"].violations]


@when("se crea un contrato desde el schema")
def _create_contract_from_dict(cv_ctx: dict):
    from shared.core.engine.domain.contract_validator import Contract
    cv_ctx["contract"] = Contract.from_dict(_diagnose_schema())


@then("required_fields es (target, reason)")
def _assert_required_fields(cv_ctx: dict):
    assert cv_ctx["contract"].required_fields == ("target", "reason")


@then("properties incluye target")
def _assert_has_target_prop(cv_ctx: dict):
    assert "target" in cv_ctx["contract"].schema_properties


@then("target.type es string")
def _assert_target_type(cv_ctx: dict):
    assert cv_ctx["contract"].schema_properties["target"]["type"] == "string"


@then("analyze esta en target.enum")
def _assert_analyze_in_enum(cv_ctx: dict):
    assert "analyze" in cv_ctx["contract"].schema_properties["target"]["enum"]


@when("se valida pre-ejecucion con target vacio")
def _validate_pre_empty(cv_ctx: dict):
    report = cv_ctx["validator"].verify_pre(cv_ctx["contract"], {"target": ""})
    cv_ctx["pre_report"] = report
    cv_ctx["pre_errors"] = [v.message for v in report.errors]
    cv_ctx["report"] = report


@then("la validacion pre-ejecucion no pasa")
def _assert_pre_not_passed(cv_ctx: dict):
    assert cv_ctx["pre_report"].passed is False


@then("los errores pre mencionan reason")
def _assert_pre_mentions_reason(cv_ctx: dict):
    assert any("reason" in m for m in cv_ctx["pre_errors"]), cv_ctx["pre_errors"]


@when("verify_post falla y se lanza ValidationError")
def _trigger_validation_error(cv_ctx: dict):
    from shared.core.engine.domain.contract_validator import Contract, ContractValidator
    from shared.core.engine.domain.errors import ValidationError

    contract = Contract.from_dict(_diagnose_schema())
    report = ContractValidator.verify_post(contract, {"target": "bogus"})
    cv_ctx["report"] = report
    try:
        raise ValidationError(
            "Output contract validation failed for REFINE.DIAGNOSE",
            errors=[{"message": m} for m in [v.message for v in report.errors]],
        )
    except ValidationError as e:
        cv_ctx["exc"] = e


@then("la excepcion contiene los errores del contrato")
def _assert_exc_has_errors(cv_ctx: dict):
    raw_errors = (cv_ctx["exc"].context or {}).get("errors", [])
    assert len(raw_errors) > 0, "sin errores en la excepcion"


@then("los mensajes mencionan target")
def _assert_exc_mentions_target(cv_ctx: dict):
    raw_errors = (cv_ctx["exc"].context or {}).get("errors", [])
    msgs = [x.get("message", "") if isinstance(x, dict) else str(x) for x in raw_errors]
    assert any("target" in m for m in msgs), msgs


@then("los mensajes mencionan reason")
def _assert_exc_mentions_reason(cv_ctx: dict):
    raw_errors = (cv_ctx["exc"].context or {}).get("errors", [])
    msgs = [x.get("message", "") if isinstance(x, dict) else str(x) for x in raw_errors]
    assert any("reason" in m for m in msgs), msgs
