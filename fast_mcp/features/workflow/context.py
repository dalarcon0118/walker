"""features/workflow/context.py — Resolved context for workflow tool handlers.

Every handler receives a WorkflowContext instead of re-resolving
dependencies independently. Follows the same pattern as MemoryContext.

Usage:
    ctx = WorkflowContext.resolve(chain_dir)
    if ctx is None:
        return {"error": "No workflow found"}
    result = orchestrator.submit(exec_ctx, fields)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkflowContext:
    """Resolved context for a project's workflow engine.

    Constructed per chain_dir by composition.py (or resolve() classmethod).
    All dependencies pre-wired; handlers never import features directly.
    """

    chain_dir: str
    orchestrator: Any  # OrchestrationService
    bundle: Any  # EngineBundle

    @classmethod
    def resolve(cls, chain_dir: str) -> WorkflowContext | None:
        """Resolve workflow context from chain_dir.

        Builds the full dependency chain:
          1. Find workflow.json path
          2. GraphRepository (loads workflow.json)
          3. StateRepository (with graph for reconciliation)
          4. WorkflowStoreAdapter (unified port)
          5. NoopTracer, NoOpSideEffectEngine, DefaultWorkflowIO
          6. EngineBundle (project-scoped)
          7. OrchestrationService (injected bundle)

        Returns None if workflow.json not found.
        """
        if not chain_dir:
            return None

        workflow_path = _find_workflow_path(chain_dir)
        if not workflow_path:
            return None

        try:
            from .infrastructure.graph_repository import GraphRepository
            from .infrastructure.state_repository import StateRepository
            from .infrastructure.workflow_store_adapter import WorkflowStoreAdapter
            from .infrastructure.noop_tracer import NoopTracer
            from .infrastructure.noop_side_effects import NoOpSideEffectEngine
            from .infrastructure.default_workflow_io import DefaultWorkflowIO
            from .application.bundle import EngineBundle
            from .application.orchestrator import OrchestrationService

            graph_repo = GraphRepository(workflow_path)
            state_repo = StateRepository(graph_repository=graph_repo)
            store = WorkflowStoreAdapter(graph_repo, state_repo)

            tracer = NoopTracer()
            side_effects = NoOpSideEffectEngine()
            workflow_io = DefaultWorkflowIO()

            bundle = EngineBundle(
                workflow_store=store,
                workflow_io=workflow_io,
                tracer=tracer,
                side_effects=side_effects,
                chain_dir=chain_dir,
            )
            orch = OrchestrationService(bundle=bundle)

            return cls(chain_dir=chain_dir, orchestrator=orch, bundle=bundle)
        except Exception as e:
            logger.warning(
                "WorkflowContext.resolve(%s) failed (%s: %s)",
                chain_dir, type(e).__name__, e,
            )
            return None


def _find_workflow_path(chain_dir: str) -> str | None:
    """Find workflow.json from chain_dir.

    Search order:
      1. {chain_dir}/mcp/data/workflow/workflow.json (standard project layout)
      2. {chain_dir}/workflow.json (flat layout)
    """
    candidates = [
        os.path.join(chain_dir, "mcp", "data", "workflow", "workflow.json"),
        os.path.join(chain_dir, "workflow.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None
