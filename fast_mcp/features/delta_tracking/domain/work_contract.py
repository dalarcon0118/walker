"""domain/work_contract.py — WorkContract: artefacto unico de trabajo (ADR-086).

El WorkContract es el artefacto que unifica los cuatro controles 4D
(reuso, goal, verificacion, presupuesto) en un solo documento versionado.
En F0a solo existe el schema y su validador puro: no persiste ni proyecta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

WORK_CONTRACT_VERSION = "1"

REQUIRED_FIELDS = ("version", "goal", "scope", "criteria", "verification", "budget", "recurrence")


@dataclass
class WorkContract:
    """Contrato de trabajo versionado (F0a)."""

    version: str = WORK_CONTRACT_VERSION
    goal: str = ""
    scope: list[str] = field(default_factory=list)
    criteria: list[str] = field(default_factory=list)
    verification: list[Any] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)
    recurrence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "goal": self.goal,
            "scope": list(self.scope),
            "criteria": list(self.criteria),
            "verification": list(self.verification),
            "budget": dict(self.budget),
            "recurrence": dict(self.recurrence),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkContract":
        if not isinstance(data, dict):
            raise ValueError("WorkContract.from_dict: data debe ser dict")
        return cls(
            version=str(data.get("version", WORK_CONTRACT_VERSION)),
            goal=str(data.get("goal", "")),
            scope=list(data.get("scope", []) or []),
            criteria=list(data.get("criteria", []) or []),
            verification=list(data.get("verification", []) or []),
            budget=dict(data.get("budget", {}) or {}),
            recurrence=dict(data.get("recurrence", {}) or {}),
        )


def validate_work_contract(data: Any) -> list[str]:
    """Valida un contrato de trabajo. Devuelve la lista de errores (vacía = válido).

    Puro, sin I/O ni dependencias de infraestructura (domain-first).
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["data debe ser un dict"]

    for field_name in REQUIRED_FIELDS:
        value = data.get(field_name)
        if value in (None, "", [], {}):
            errors.append(f"{field_name}: is required and cannot be empty")

    version = data.get("version")
    if version is not None and str(version) != WORK_CONTRACT_VERSION:
        errors.append(f"version debe ser {WORK_CONTRACT_VERSION!r}, recibido {version!r}")

    scope = data.get("scope")
    if scope not in (None, "", [], {}) and (
        not isinstance(scope, list) or not all(isinstance(x, str) for x in scope)
    ):
        errors.append("scope debe ser una lista de strings")

    criteria = data.get("criteria")
    if criteria not in (None, "", [], {}) and (
        not isinstance(criteria, list) or not all(isinstance(x, str) for x in criteria)
    ):
        errors.append("criteria debe ser una lista de strings")

    verification = data.get("verification")
    if verification not in (None, "", [], {}) and not isinstance(verification, list):
        errors.append("verification debe ser una lista")

    budget = data.get("budget")
    if budget not in (None, "", [], {}) and not isinstance(budget, dict):
        errors.append("budget debe ser un dict")

    recurrence = data.get("recurrence")
    if recurrence not in (None, "", [], {}) and not isinstance(recurrence, dict):
        errors.append("recurrence debe ser un dict")

    return errors
