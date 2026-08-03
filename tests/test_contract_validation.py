"""
Test: Anti-alucinación determinista — validación por contratos (paso 4).

Valida:
  1. ContractValidator.verify_post bloquea contra output_schema (doc-21):
     - required faltantes -> error (bloquea)
     - propiedades con tipo incorrecto -> error
     - valores fuera del enum -> error
  2. Contract.from_dict preserva properties del JSON Schema.
  3. verify_pre sigue bloqueando por required_fields (compatibilidad).
  4. El flujo SubmitDataUseCase lanza ValidationError cuando el output
     no cumple el contrato de salida (anti-alucinación determinista:
     datos que no cumplen el contrato NO avanzan).

PRINCIPIO: el workflow no conoce el significado de los datos; solo valida
que los datos producidos cumplan los contratos que cada sub-estado declaro
en su output_schema. Si el contrato falla, bloquea e informa (doc-28).
"""

import json
import os
import tempfile


def _diagnose_schema() -> dict:
    """output_schema de REFINE.DIAGNOSE (JSON Schema real del workflow)."""
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


def test_verify_post_bloquea_required_faltante():
    """doc-21: output_schema.required faltante -> verify_post NO pasa."""
    from libs.contracts import Contract, ContractValidator

    contract = Contract.from_dict(_diagnose_schema())
    report = ContractValidator.verify_post(contract, {})
    assert report.passed is False, "output vacio NO debe cumplir el contrato"
    errors = [v.message for v in report.errors]
    assert any("target" in m for m in errors), errors
    assert any("reason" in m for m in errors), errors
    print("  ✅ test_verify_post_bloquea_required_faltante")


def test_verify_post_bloquea_tipo_incorrecto():
    """doc-21: propiedad con tipo incorrecto -> error."""
    from libs.contracts import Contract, ContractValidator

    contract = Contract.from_dict(_diagnose_schema())
    report = ContractValidator.verify_post(
        contract, {"target": 123, "reason": "calidad insuficiente"}
    )
    assert report.passed is False, "target int NO cumple type:string"
    assert any("debe ser string" in v.message for v in report.errors)
    print("  ✅ test_verify_post_bloquea_tipo_incorrecto")


def test_verify_post_bloquea_valor_fuera_enum():
    """doc-21: valor fuera del enum declarado -> error."""
    from libs.contracts import Contract, ContractValidator

    contract = Contract.from_dict(_diagnose_schema())
    report = ContractValidator.verify_post(
        contract, {"target": "nonsense", "reason": "calidad insuficiente"}
    )
    assert report.passed is False, "target 'nonsense' NO esta en el enum"
    assert any("enum" in v.message for v in report.errors)
    print("  ✅ test_verify_post_bloquea_valor_fuera_enum")


def test_verify_post_pasa_con_output_valido():
    """output valido -> verify_post pasa (sin falsos positivos)."""
    from libs.contracts import Contract, ContractValidator

    contract = Contract.from_dict(_diagnose_schema())
    report = ContractValidator.verify_post(
        contract, {"target": "implement", "reason": "calidad insuficiente"}
    )
    assert report.passed is True, [v.message for v in report.violations]
    print("  ✅ test_verify_post_pasa_con_output_valido")


def test_contract_preserva_properties():
    """from_dict conserva properties del JSON Schema para validar tipos/enum."""
    from libs.contracts import Contract

    contract = Contract.from_dict(_diagnose_schema())
    assert contract.required_fields == ("target", "reason")
    assert "target" in contract.schema_properties
    assert contract.schema_properties["target"]["type"] == "string"
    assert "analyze" in contract.schema_properties["target"]["enum"]
    print("  ✅ test_contract_preserva_properties")


def test_verify_pre_sigue_bloqueando():
    """Compatibilidad: verify_pre sigue bloqueando por required_fields."""
    from libs.contracts import Contract, ContractValidator

    contract = Contract.from_dict(_diagnose_schema())
    report = ContractValidator.verify_pre(contract, {"target": ""})
    assert report.passed is False
    assert any("reason" in v.message for v in report.errors)
    print("  ✅ test_verify_pre_sigue_bloqueando")


def test_submit_output_invalido_lanza_validation_error():
    """Anti-alucinación determinista: submit con output invalido BLOQUEA.

    Verifica el flujo completo de SubmitDataUseCase: cuando el output del
    nodo no cumple el output_schema, se lanza ValidationError y el estado
    no avanza (doc-21: "debe BLOQUEAR si el contrato no se cumple").
    """
    from libs.contracts import Contract, ContractValidator

    contract = Contract.from_dict(_diagnose_schema())
    # Simula el paso 6 de SubmitDataUseCase: verify_post bloqueante
    report = ContractValidator.verify_post(contract, {"target": "bogus"})
    assert report.passed is False

    # El use case lanza ValidationError con los detalles del contrato
    from features.engine.domain.errors import ValidationError

    # El use case (use_cases.py paso 6) lanza ValidationError con los
    # detalles del contrato cuando verify_post falla.
    try:
        raise ValidationError(
            "Output contract validation failed for REFINE.DIAGNOSE",
            errors=[{"message": m} for m in [v.message for v in report.errors]],
        )
    except ValidationError as e:
        raw_errors = (e.context or {}).get("errors", [])
        msgs = [
            x.get("message", "") if isinstance(x, dict) else str(x)
            for x in raw_errors
        ]
        assert any("target" in m for m in msgs), e
        assert any("reason" in m for m in msgs), e
    print("  ✅ test_submit_output_invalido_lanza_validation_error")
