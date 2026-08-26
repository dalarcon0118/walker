"""domain/enums.py — Enums del dominio Workflow Navigation.

SSOT de tipos y estados del workflow engine.
"""
from __future__ import annotations

from enum import Enum


class ExecutionType(str, Enum):
    """
    Tipo de ejecucion de un sub-estado (v3).

    5 tipos determinan QUIEN ejecuta el paso:
    - bootstrap:  Engine inits session (TaskRegistry action)
    - auto:       TaskRegistry runs action (scripted)
    - llm_task:   LLM receives prompt (submit with schema)
    - user_input: User chooses option (submit with options)
    - manual:     Agent does external work (no automation)
    """

    BOOTSTRAP = "bootstrap"
    AUTO = "auto"
    LLM_TASK = "llm_task"
    USER_INPUT = "user_input"
    MANUAL = "manual"


# Alias para compatibilidad hacia atras
SubStateMode = ExecutionType


class NodeType(str, Enum):
    """Tipo de nodo en el grafo de workflow."""

    START = "start"
    SPRINT = "sprint"
    ANALYZE = "analyze"
    IMPLEMENT = "implement"
    VERIFY = "verify"
    REFINE = "refine"
    SPRINT_PLANNING = "sprint_planning"


class GateType(str, Enum):
    """Tipo de gate: inter-nodo (transicion) o intra-nodo (sub-state)."""

    INTER_NODE = "inter_node"
    INTRA_NODE = "intra_node"


class DomainEventType(str, Enum):
    """Catalogo de eventos de dominio. SSOT."""

    STATE_TRANSITIONED = "state_transitioned"
    DATA_SUBMITTED = "data_submitted"
    GATE_VALIDATED = "gate_validated"
    AUTO_ADVANCED = "auto_advanced"
    FAILURE_LOOPED = "failure_looped"
    VALIDATION_FAILED = "validation_failed"
    PRECONDITION_FAILED = "precondition_failed"


__all__ = [
    "ExecutionType",
    "SubStateMode",
    "NodeType",
    "GateType",
    "DomainEventType",
]
