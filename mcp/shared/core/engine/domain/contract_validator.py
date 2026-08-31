"""features/engine/domain/contract_validator.py — Contract Validator (Design by Contract).

DDD: Modelos puros (dataclasses) + servicio stateless.
SRP: Unica responsabilidad = validar contratos formales del workflow.

MIGRADO desde shared/common/context/ (era unico consumidor: features/engine).

Arquitectura:
    Contract          -> Value Object: preconditions, postconditions, invariants
    ContractReport    -> Value Object: resultado de validacion
    ContractValidator -> Domain Service: verify_pre(), verify_post(), verify_invariant()

Uso:
    from shared.core.engine.domain.contract_validator import (
        ContractValidator, Contract, ContractReport
    )

    contract = Contract(
        preconditions=["state.current_node == SPRINT"],
        postconditions=["state.sub_state_index >= 0"],
        invariants=["state.version == 5.0"],
    )
    report = ContractValidator.verify_pre(contract, state_dict)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════
# Value Objects
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ContractViolation:
    """Violacion individual de un contrato."""
    condition: str
    message: str
    severity: str = "error"  # error | warning


@dataclass(frozen=True)
class ContractReport:
    """Reporte completo de validacion de contrato.

    Value Object — inmutable.
    """
    passed: bool
    violations: tuple[ContractViolation, ...] = field(default_factory=tuple)
    total_checks: int = 0

    @property
    def errors(self) -> list[ContractViolation]:
        return [v for v in self.violations if v.severity == "error"]

    @property
    def warnings(self) -> list[ContractViolation]:
        return [v for v in self.violations if v.severity == "warning"]


@dataclass(frozen=True)
class Contract:
    """Contrato formal entre sub-estados.

    Value Object — define QUE validar, no COMO validar.
    Las condiciones son expresiones evaluables contra el estado.

    Args:
        preconditions: Condiciones que deben cumplirse ANTES de la transicion.
                       Si alguna falla, la transicion NO ocurre (blocking).
        postconditions: Condiciones que deben cumplirse DESPUES de la transicion.
                        Si alguna falla, se registra warning (advisory).
        invariants: Condiciones que deben cumplirse SIEMPRE.
                    Se verifican en cualquier punto del workflow.
        description: Descripcion del contrato (opcional).
    """
    preconditions: tuple[str, ...] = field(default_factory=tuple)
    postconditions: tuple[str, ...] = field(default_factory=tuple)
    invariants: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""
    required_fields: tuple[str, ...] = field(default_factory=tuple)
    schema_properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Normaliza listas a tuplas para comparacion consistente."""
        for field in ('preconditions', 'postconditions', 'invariants', 'required_fields'):
            val = getattr(self, field)
            if isinstance(val, list):
                object.__setattr__(self, field, tuple(val))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Contract:
        """Construye un Contract desde un dict JSON.

        Si data es un JSON Schema (input_schema/output_schema) con 'required',
        esos campos se extraen como required_fields para validarlos en
        verify_pre. Soporta ambos formatos: contrato formal (preconditions)
        y JSON Schema.
        """
        req = data.get("required", []) or []
        if not isinstance(req, (list, tuple)):
            req = []
        props = data.get("properties", {}) or {}
        if not isinstance(props, dict):
            props = {}
        return cls(
            preconditions=tuple(data.get("preconditions", []) or []),
            postconditions=tuple(data.get("postconditions", []) or []),
            invariants=tuple(data.get("invariants", []) or []),
            description=data.get("description", ""),
            required_fields=tuple(req),
            schema_properties=props,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serializa a dict para persistencia.

        Siempre incluye todos los campos (incluso vacios)
        para round-trip consistente con from_dict().
        """
        return {
            "preconditions": list(self.preconditions),
            "postconditions": list(self.postconditions),
            "invariants": list(self.invariants),
            "description": self.description,
            "required": list(self.required_fields),
        }

    @property
    def total_conditions(self) -> int:
        return len(self.preconditions) + len(self.postconditions) + len(self.invariants)


# ═══════════════════════════════════════════════════════════════════════════
# Domain Service: ContractValidator (puro, sin IO)
# ═══════════════════════════════════════════════════════════════════════════


class ContractValidator:
    """Valida contratos formales contra el estado del workflow.

    Domain Service — puro, sin efectos secundarios.
    SRP: Unica responsabilidad = validar condiciones contra un estado.

    Las condiciones son expresiones Python evaluadas con eval() seguro
    contra un namespace controlado (solo acceso a state.*).

    verify_pre()  → BLOCKING: si falla, la transicion NO ocurre
    verify_post() → ADVISORY: si falla, la transicion ya ocurrio (warning)
    verify_invariant() → ANY POINT: chequea invariantes en cualquier estado
    """

    _ALLOWED_BUILTINS = {
        "len": len, "str": str, "int": int, "float": float,
        "bool": bool, "list": list, "dict": dict, "tuple": tuple,
        "isinstance": isinstance, "hasattr": hasattr,
        "all": all, "any": any, "min": min, "max": max,
        "sorted": sorted, "reversed": reversed,
        "True": True, "False": False, "None": None,
    }

    @classmethod
    def verify_pre(cls, contract: Contract, state: dict[str, Any]) -> ContractReport:
        """Verifica precondiciones.

        BLOCKING: si alguna precondition falla, la transicion NO debe ocurrir.
        Tambien valida required_fields del JSON Schema (input_schema.required):
        si un campo requerido falta o esta vacio, falla.

        Args:
            contract: Contrato con preconditions a verificar.
            state: Estado actual del workflow (WorkflowState.to_dict()).

        Returns:
            ContractReport con passed=False si alguna precondition falla.
        """
        violations: list[ContractViolation] = []

        # ── Validar required_fields del JSON Schema ──────────────
        for field in contract.required_fields:
            val = state.get(field)
            if val is None or val == "":
                violations.append(ContractViolation(
                    condition=f"{field} is required",
                    message=f"Campo requerido faltante: {field}",
                ))
            elif isinstance(val, (list, tuple)) and len(val) == 0:
                violations.append(ContractViolation(
                    condition=f"{field} is required",
                    message=f"Campo requerido '{field}' no puede estar vacio",
                ))

        if not contract.preconditions:
            return ContractReport(passed=not violations, total_checks=len(violations),
                                  violations=violations)

        for expr in contract.preconditions:
            if not cls._evaluate(expr, state):
                violations.append(ContractViolation(
                    condition=expr,
                    message=f"Precondition FAILED: {expr}",
                    severity="error",
                ))

        return ContractReport(
            passed=len(violations) == 0,
            violations=tuple(violations),
            total_checks=len(contract.preconditions),
        )

    @classmethod
    def verify_post(cls, contract: Contract, state: dict[str, Any]) -> ContractReport:
        """Verifica postcondiciones y contrato de salida (output_schema).

        BLOQUEANTE contra el JSON Schema de salida:
          - required_fields (output_schema.required): si un campo requerido
            falta o esta vacio, la salida NO cumple el contrato (error).
          - properties (tipos y enum): si un valor presente no cumple el tipo
            o enum declarado, es un error.
          - postconditions: si fallan se registran como warning (advisory),
            porque la transicion ya ocurrio para ellas.

        PRINCIPIO (convencion doc-21): el contrato de salida es la unica
        herramienta del workflow para saber si un nodo completo su trabajo.
        Un contrato debil permite que datos que no cumplen avancen.

        Args:
            contract: Contrato (precondiciones y/o JSON Schema de salida).
            state: Datos de salida producidos por el nodo.

        Returns:
            ContractReport con passed=False si el schema o las postconditions
            fallan (las postconditions como warning no bloquean, pero el
            schema de salida si).
        """
        violations: list[ContractViolation] = []

        # ── 1. Validar required_fields del JSON Schema (BLOQUEANTE) ──
        # Un output_schema con 'required' exige que el nodo produzca esos
        # campos. Si faltan o estan vacios, el contrato de salida no se
        # cumple y el workflow debe bloquear (doc-20/21).
        for field in contract.required_fields:
            val = state.get(field)
            if val is None or val == "":
                violations.append(ContractViolation(
                    condition=f"{field} is required (output)",
                    message=f"Campo de salida requerido faltante: {field}",
                ))
            elif isinstance(val, (list, tuple)) and len(val) == 0:
                violations.append(ContractViolation(
                    condition=f"{field} is required (output)",
                    message=f"Campo de salida requerido '{field}' no puede estar vacio",
                ))

        # ── 2. Validar properties del JSON Schema (tipos/enum) ──────
        # No se valida el schema completo (jsonschema), pero si los tipos
        # basicos y enum que el workflow declaro: string, integer, array,
        # object, boolean y enum. Si el valor presente no cumple, error.
        if contract.schema_properties:
            for prop_name, prop_def in contract.schema_properties.items():
                val = state.get(prop_name)
                if val is None:
                    continue  # No es required -> su ausencia no es error
                expected_type = prop_def.get("type", "")
                if expected_type and not cls._type_ok(expected_type, val):
                    violations.append(ContractViolation(
                        condition=f"{prop_name} has type '{expected_type}'",
                        message=(
                            f"Campo de salida '{prop_name}' debe ser "
                            f"{expected_type}, got {type(val).__name__}"
                        ),
                    ))
                    continue
                enum = prop_def.get("enum")
                if isinstance(enum, (list, tuple)) and val not in enum:
                    violations.append(ContractViolation(
                        condition=f"{prop_name} in enum",
                        message=(
                            f"Campo de salida '{prop_name}' = {val!r} no esta "
                            f"en el enum {enum}"
                        ),
                    ))

        # ── 3. Validar postconditions (ADVISORY: warning) ───────────
        if contract.postconditions:
            for expr in contract.postconditions:
                if not cls._evaluate(expr, state):
                    violations.append(ContractViolation(
                        condition=expr,
                        message=f"Postcondition WARNING: {expr}",
                        severity="warning",
                    ))

        return ContractReport(
            passed=len(violations) == 0,
            violations=tuple(violations),
            total_checks=len(contract.required_fields) + len(contract.postconditions),
        )

    @staticmethod
    def _type_ok(expected_type: str, val: Any) -> bool:
        """Verifica que un valor cumpla el tipo basico declarado."""
        if expected_type == "string":
            return isinstance(val, str)
        if expected_type == "integer":
            return isinstance(val, int) and not isinstance(val, bool)
        if expected_type == "number":
            return isinstance(val, (int, float)) and not isinstance(val, bool)
        if expected_type == "boolean":
            return isinstance(val, bool)
        if expected_type == "array":
            return isinstance(val, (list, tuple))
        if expected_type == "object":
            return isinstance(val, dict)
        if expected_type == "null":
            return val is None
        return True  # tipo desconocido -> no validar

    @classmethod
    def verify_invariant(
        cls, contract: Contract, state: dict[str, Any]
    ) -> ContractReport:
        """Verifica invariantes.

        ANY POINT: chequea invariantes que deben cumplirse siempre.
        Ni blocking ni advisory — es una verificacion de integridad.

        Args:
            contract: Contrato con invariants a verificar.
            state: Estado actual del workflow.

        Returns:
            ContractReport con violations si algun invariante falla.
        """
        if not contract.invariants:
            return ContractReport(passed=True, total_checks=0)

        violations: list[ContractViolation] = []
        for expr in contract.invariants:
            if not cls._evaluate(expr, state):
                violations.append(ContractViolation(
                    condition=expr,
                    message=f"Invariant VIOLATED: {expr}",
                    severity="error",
                ))

        return ContractReport(
            passed=len(violations) == 0,
            violations=tuple(violations),
            total_checks=len(contract.invariants),
        )

    @classmethod
    def verify_all(
        cls, contract: Contract, state_before: dict[str, Any],
        state_after: dict[str, Any] | None = None,
    ) -> dict[str, ContractReport]:
        """Verifica TODO el contrato (pre + post + invariant).

        Args:
            contract: Contrato completo.
            state_before: Estado ANTES de la transicion.
            state_after: Estado DESPUES de la transicion (opcional).

        Returns:
            Dict con 'pre', 'post', 'invariant' → ContractReport.
        """
        results: dict[str, ContractReport] = {
            "pre": cls.verify_pre(contract, state_before),
            "invariant": cls.verify_invariant(contract, state_before),
        }
        if state_after is not None:
            results["post"] = cls.verify_post(contract, state_after)
        return results

    # ── Helpers ────────────────────────────────────────────────

    @classmethod
    def _evaluate(cls, expression: str, state: dict[str, Any]) -> bool:
        """Evalua una expresion contra el estado.

        Usa un namespace controlado para seguridad:
        - state.* para acceder al estado
        - builtins limitados (len, str, int, etc.)
        - NO permite import, __import__, eval(), exec()

        Args:
            expression: Expresion Python a evaluar (ej: 'state[\"current_node\"] == \"SPRINT\"')
            state: Dict de estado.

        Returns:
            True si la expresion es verdadera, False en cualquier otro caso.
        """
        namespace = {
            "state": state,
            **cls._ALLOWED_BUILTINS,
        }
        try:
            result = eval(expression, {"__builtins__": {}}, namespace)
            return bool(result)
        except (KeyError, TypeError, NameError, SyntaxError, AttributeError):
            return False
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════════════════════
# Schema helpers
# ═══════════════════════════════════════════════════════════════════════════

# Schema minimo para contracts.schema.json
CONTRACT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "$id": "contracts.schema.json",
    "title": "Contracts",
    "description": "Contractos formales para workflow transitions",
    "type": "object",
    "properties": {
        "preconditions": {
            "type": "array",
            "items": {"type": "string", "minLength": 3},
            "description": "Condiciones que deben cumplirse ANTES de la transicion (blocking)",
        },
        "postconditions": {
            "type": "array",
            "items": {"type": "string", "minLength": 3},
            "description": "Condiciones que deben cumplirse DESPUES de la transicion (advisory)",
        },
        "invariants": {
            "type": "array",
            "items": {"type": "string", "minLength": 3},
            "description": "Condiciones que deben cumplirse SIEMPRE",
        },
        "description": {
            "type": "string",
            "description": "Descripcion del contrato",
        },
    },
    "additionalProperties": False,
}
