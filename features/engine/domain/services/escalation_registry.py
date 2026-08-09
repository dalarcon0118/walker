"""domain/services/escalation_registry.py — Mapeo de errores a nodos de escalación.

P1: Escalation Events BPMN.
Registra mapeos de ErrorType → nodo de escalación en el workflow.
Si un error no tiene mapeo, usa el default "REFINE".
"""
from __future__ import annotations

from typing import Any

from features.engine.domain.errors import ErrorType


class EscalationNodeRegistry:
    """Mapea tipos de error a nodos de escalación."""

    _DEFAULT_ESCALATION_NODE = "REFINE"

    _MAPPING: dict[ErrorType, str] = {
        ErrorType.VALIDATION: "REFINE",
        ErrorType.EXECUTION: "REFINE",
        ErrorType.PRECONDITION: "REFINE",
        ErrorType.RESOURCE: "REFINE",
        ErrorType.TIMEOUT: "REFINE",
        ErrorType.GATE_VALIDATION: "REFINE",
        ErrorType.SUBSTATE_NOT_FOUND: "REFINE",
        ErrorType.NODE_NOT_FOUND: "REFINE",
        ErrorType.CONTRACT: "REFINE",
        ErrorType.UNKNOWN: "REFINE",
        ErrorType.ESCALATE: "REFINE",
    }

    @classmethod
    def get_escalation_node(cls, error_type: ErrorType) -> str:
        return cls._MAPPING.get(error_type, cls._DEFAULT_ESCALATION_NODE)

    @classmethod
    def register(cls, error_type: ErrorType, node_id: str) -> None:
        cls._MAPPING[error_type] = node_id

    @classmethod
    def get_default_node(cls) -> str:
        return cls._DEFAULT_ESCALATION_NODE
