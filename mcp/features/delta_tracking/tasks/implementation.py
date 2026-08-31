"""Shim de backward-compat (ADR-051 G2).

La task vive en features/main/task_execution/tasks/implementation.py; este
módulo re-exporta los símbolos legacy desde el task layer de main.
"""
from shared.tasks.actions.implement import (
    ImplementInput,
    implement_task,
    register,
    run_tests,
)

__all__ = ["ImplementInput", "implement_task", "register", "run_tests"]
