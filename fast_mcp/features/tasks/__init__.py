"""fast_mcp.features.tasks — Task execution + side effects.

Ported from mcp/shared/tasks (ADR-WALKER-071).
Provides TaskRegistry and SideEffectRegistry for the workflow engine.
"""
from fast_mcp.features.tasks.domain.task_registry import TaskRegistry, ExecutionContext, TaskResult
from fast_mcp.features.tasks.domain.side_effect_registry import SideEffectRegistry

__all__ = ["TaskRegistry", "ExecutionContext", "TaskResult", "SideEffectRegistry"]
