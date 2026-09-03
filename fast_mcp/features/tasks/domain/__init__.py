"""domain/__init__.py — Domain layer of tasks feature.

Provides: TaskRegistry, ExecutionContext, TaskResult, SideEffectRegistry.
"""
from fast_mcp.features.tasks.domain.task_registry import TaskRegistry, ExecutionContext, TaskResult
from fast_mcp.features.tasks.domain.side_effect_registry import SideEffectRegistry

__all__ = ["TaskRegistry", "ExecutionContext", "TaskResult", "SideEffectRegistry"]
