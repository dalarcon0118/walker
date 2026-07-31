"""
_walker/contracts.py — Contract Validator (Design by Contract).

DDD: Modelos puros (dataclasses) + servicio stateless.
SRP: Unica responsabilidad = validar contratos formales.
SSOT: Los contratos se definen en JSON (workflow.json o data_contract).

Arquitectura:
    Contract         → Value Object: preconditions, postconditions, invariants
    ContractReport   → Value Object: resultado de validacion
    ContractValidator → Domain Service: verify_pre(), verify_post(), verify_invariant()

Uso:
    from libs.contracts import ContractValidator, Contract, ContractReport

    contract = Contract(
        preconditions=["state.current_node == 'SPRINT'"],
        postconditions=["state.sub_state_index >= 0"],
        invariants=["state.version == '5.0'"],
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

    def __post_init__(self):
        """Normaliza listas a tuplas para comparacion consistente."""
        for field in ('preconditions', 'postconditions', 'invariants'):
            val = getattr(self, field)
            if isinstance(val, list):
                object.__setattr__(self, field, tuple(val))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Contract:
        """Construye un Contract desde un dict JSON."""
        return cls(
            preconditions=tuple(data.get("preconditions", []) or []),
            postconditions=tuple(data.get("postconditions", []) or []),
            invariants=tuple(data.get("invariants", []) or []),
            description=data.get("description", ""),
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

        Args:
            contract: Contrato con preconditions a verificar.
            state: Estado actual del workflow (SprintState.to_dict()).

        Returns:
            ContractReport con passed=False si alguna precondition falla.
        """
        if not contract.preconditions:
            return ContractReport(passed=True, total_checks=0)

        violations: list[ContractViolation] = []
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
        """Verifica postcondiciones.

        ADVISORY: si alguna postcondition falla, la transicion YA ocurrio.
        Se registra como warning, no como error.

        Args:
            contract: Contrato con postconditions a verificar.
            state: Estado del workflow DESPUES de la transicion.

        Returns:
            ContractReport con violations como warnings (no blocking).
        """
        if not contract.postconditions:
            return ContractReport(passed=True, total_checks=0)

        violations: list[ContractViolation] = []
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
            total_checks=len(contract.postconditions),
        )

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
