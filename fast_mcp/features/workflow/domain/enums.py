"""domain/enums — Domain enums for the workflow engine."""
from __future__ import annotations

from enum import Enum


class ExecutionType(str, Enum):
    BOOTSTRAP = "bootstrap"
    AUTO = "auto"
    LLM_TASK = "llm_task"
    USER_INPUT = "user_input"
    MANUAL = "manual"


SubStateMode = ExecutionType


class DomainEventType(str, Enum):
    STATE_TRANSITIONED = "state_transitioned"
    DATA_SUBMITTED = "data_submitted"
    GATE_VALIDATED = "gate_validated"
    AUTO_ADVANCED = "auto_advanced"
    FAILURE_LOOPED = "failure_looped"
    VALIDATION_FAILED = "validation_failed"
