"""domain/errors.py — Workflow domain errors.

Semantic types (not generic) so callers know exactly what failed.
"""
from __future__ import annotations

from enum import Enum


class ErrorType(str, Enum):
    VALIDATION = "validation"
    EXECUTION = "execution"
    TRANSITION = "transition"
    STATE = "state"
    INFRASTRUCTURE = "infrastructure"
    UNKNOWN = "unknown"


class WorkflowEngineError(Exception):
    """Base for all workflow engine errors."""
    def __init__(self, message: str, context: dict | None = None):
        self.message = message
        self.context = context or {}
        super().__init__(message)


class NodeNotFoundError(WorkflowEngineError):
    def __init__(self, node_id: str):
        super().__init__(f"Node '{node_id}' not found", {"node_id": node_id})


class SubStateNotFoundError(WorkflowEngineError):
    def __init__(self, node_id: str, sub_key: str):
        super().__init__(f"Sub-state '{sub_key}' not found in node '{node_id}'",
                         {"node_id": node_id, "sub_key": sub_key})


class GateNotFoundError(WorkflowEngineError):
    def __init__(self, node_id: str, gate: str):
        super().__init__(f"Gate '{gate}' not found for node '{node_id}'",
                         {"node_id": node_id, "gate": gate})


class TransitionBlockedError(WorkflowEngineError):
    def __init__(self, message: str, details: list[str] | None = None):
        super().__init__(message, {"details": details or []})


class GateValidationError(WorkflowEngineError):
    def __init__(self, gate: str, stderr: str):
        super().__init__(f"Gate '{gate}' validation failed: {stderr}",
                         {"gate": gate, "stderr": stderr})


class ValidationError(WorkflowEngineError):
    def __init__(self, message: str, errors: list[dict] | None = None):
        super().__init__(message, {"errors": errors or []})


class StateConflictError(WorkflowEngineError):
    """Lost update detected by optimistic concurrency (CAS)."""
    def __init__(self, chain_dir: str, expected: int, stored: int):
        super().__init__(
            f"Concurrency conflict in '{chain_dir}': expected revision "
            f"{expected}, stored {stored}",
            {"chain_dir": chain_dir, "expected": expected, "stored": stored},
        )
