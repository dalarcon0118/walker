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
    task_registry: Any = None  # TaskRegistry (Sprint 5.4)

    @classmethod
    def resolve(cls, chain_dir: str, task_registry: Any = None, prompt_manager: Any = None) -> WorkflowContext | None:
        """Resolve workflow context from chain_dir.

        Builds the full dependency chain:
          1. Find workflow.json path
          2. GraphRepository (loads workflow.json)
          3. StateRepository (with graph for reconciliation)
          4. WorkflowStoreAdapter (unified port)
          5. NoopTracer, NoOpSideEffectEngine, DefaultWorkflowIO
          6. EngineBundle (project-scoped)
          7. OrchestrationService (injected bundle)

        `task_registry` is injected by the composition root (composition.py),
        which is the ONLY place allowed to cross-import features (R1). The
        workflow feature itself never imports the tasks feature.

        `prompt_manager` (defaults to PromptManager) resolves per-node prompts
        for LLM_TASK sub-states and is attached to the bundle so `status` /
        `help` surface the current step's prompt.
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

            if prompt_manager is None:
                from .application.prompts.manager import PromptManager

                prompt_manager = PromptManager(chain_dir=chain_dir)

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
                task_registry=task_registry,
                prompt_manager=prompt_manager,
            )
            orch = OrchestrationService(bundle=bundle)

            return cls(
                chain_dir=chain_dir,
                orchestrator=orch,
                bundle=bundle,
                task_registry=task_registry,
            )
        except Exception as e:
            logger.warning(
                "WorkflowContext.resolve(%s) failed (%s: %s)",
                chain_dir, type(e).__name__, e,
            )
            return None


def _find_workflow_path(chain_dir: str) -> str | None:
    """Find workflow.json from chain_dir — delega en PathManager (SSOT único).

    Política en shared_kernel.path_manager.PathManager (única fuente):
      1. Override explícito: env WALKER_WORKFLOW_MANIFEST / config 'workflow.manifest'
         (se honra tal cual; si no existe, el loader falla ruidosamente).
      2. {chain_dir}/mcp/data/workflow/workflow.json  (canónico)
      3. {chain_dir}/workflow.json                    (flat)
      4. None (caller decide el error).

    La ubicación legacy {chain_dir}/data/workflow/ fue ELIMINADA de la
    búsqueda (INV-016: ubicación fantasma, parasite-prone — no se busca).
    """
    from fast_mcp.shared_kernel.path_manager import PathManager

    resolved = PathManager().resolve_for_project(chain_dir)
    return str(resolved) if resolved else None
