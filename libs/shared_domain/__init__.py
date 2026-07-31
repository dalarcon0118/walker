"""
libs/shared_domain/ — Shared domain value objects.

SSOT for cross-feature VOs. Every feature imports from here
instead of from other features' domain models.

PRINCIPIO: libs/ NO importa de features/. Solo Python stdlib.
"""
from __future__ import annotations

from .models import ExecutionContext, TaskResult, ProjectId, SprintId

__all__ = [
    "ExecutionContext",
    "TaskResult",
    "ProjectId",
    "SprintId",
]
