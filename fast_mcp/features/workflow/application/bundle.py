"""application/bundle.py — EngineBundle: project-scoped VO with all engine dependencies.

Constructed per chain_dir (NOT per server boot). Adapters (workflow_store,
tracer) are shared singletons; the bundle is project-scoped.

Original: 53 LOC with 8 fields + to_orchestrator_kwargs().
Simplified: 40 LOC with 4 required fields, no helper methods.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain.ports import (
    ITracer,
    IWorkflowIO,
    IWorkflowStore,
    ISideEffectEngine,
)


@dataclass(frozen=True)
class EngineBundle:
    """Project-scoped VO with all engine dependencies.

    The engine is ANEMIC: it does NOT couple to any service locator,
    DI container, or external architecture. The HOST (composition.py)
    constructs this VO and passes it to OrchestrationService.

    Fields:
        workflow_store: Load/save graph + state (unified port).
        workflow_io: Run gate validation scripts.
        tracer: Trace events for auditability.
        side_effects: Execute compensation handlers (no-op stub initially).
        chain_dir: Project directory (makes the bundle project-scoped).
    """

    workflow_store: IWorkflowStore
    workflow_io: IWorkflowIO
    tracer: ITracer
    side_effects: ISideEffectEngine
    chain_dir: str
    task_registry: Any = None  # TaskRegistry (Sprint 5): executes sub_state.action
    prompt_manager: Any = None  # IPromptManager: resolve per-node prompts (LLM_TASK)
