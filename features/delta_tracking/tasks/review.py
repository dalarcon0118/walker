"""Shim de backward-compat (ADR-051 G2).

La task vive en features/main/task_execution/tasks/review.py; este módulo
re-exporta los símbolos legacy desde el task layer de main.
"""
from tasks.actions.code_review import (
    ReviewInput,
    register,
    review_code,
)

__all__ = ["ReviewInput", "register", "review_code"]
