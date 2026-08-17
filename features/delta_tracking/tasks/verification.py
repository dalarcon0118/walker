"""Shim de backward-compat (ADR-051 G2).

La task vive en features/main/task_execution/tasks/verification.py; este
módulo re-exporta los símbolos legacy desde el task layer de main.
"""
from features.main.task_execution.tasks.verify import (
    VerifyInput,
    _translate_predicates,
    register,
    verify_predicates,
)

__all__ = [
    "VerifyInput",
    "_translate_predicates",
    "register",
    "verify_predicates",
]
