"""tests/test_work_contract_schema_bdd.py — schema y validador puro de WorkContract (F0a)."""

from __future__ import annotations

from fast_mcp.features.delta_tracking.domain.work_contract import (
    WORK_CONTRACT_VERSION,
    WorkContract,
    validate_work_contract,
)


def _complete() -> dict:
    return {
        "version": WORK_CONTRACT_VERSION,
        "goal": "F0a: schema y validador",
        "scope": ["fast_mcp/features/delta_tracking/domain/work_contract.py"],
        "criteria": ["el schema acepta un contrato completo"],
        "verification": [{"criterion": "tests pasan", "check": "test"}],
        "budget": {"max_files": 2, "max_loc": 150, "max_new_files": 1},
        "recurrence": {"level": "full"},
    }


def test_contrato_completo_valida():
    errors = validate_work_contract(_complete())
    assert errors == []


def test_contrato_vacio_reporta_todos_los_campos():
    errors = validate_work_contract({})
    for field in ("version", "goal", "scope", "criteria", "verification", "budget", "recurrence"):
        assert any(field in e for e in errors)


def test_campos_faltantes_reportan_error_accionable():
    data = _complete()
    del data["goal"]
    del data["criteria"]
    errors = validate_work_contract(data)
    assert any("goal" in e for e in errors)
    assert any("criteria" in e for e in errors)


def test_version_incorrecta_reporta_error():
    data = _complete()
    data["version"] = "99"
    errors = validate_work_contract(data)
    assert any("version" in e for e in errors)


def test_tipos_invalidos_reportan_error():
    data = _complete()
    data["scope"] = "no-es-lista"
    data["budget"] = 42
    data["recurrence"] = 7
    errors = validate_work_contract(data)
    assert any("scope" in e for e in errors)
    assert any("budget" in e for e in errors)
    assert any("recurrence" in e for e in errors)


def test_data_no_dict_reporta_error():
    errors = validate_work_contract("no-es-dict")
    assert errors and "dict" in errors[0]


def test_work_contract_roundtrip():
    data = _complete()
    contract = WorkContract.from_dict(data)
    assert contract.to_dict() == data


def test_work_contract_default_version():
    contract = WorkContract()
    assert contract.version == WORK_CONTRACT_VERSION
    assert contract.to_dict()["version"] == WORK_CONTRACT_VERSION
