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
    cpg_adapter: Any = None  # ICpgAnalysisPort (W5 ADR-076)

    @classmethod
    def resolve(
        cls,
        chain_dir: str,
        task_registry: Any = None,
        prompt_manager: Any = None,
        cpg_adapter: Any = None,
        ckg_searcher: Any = None,
    ) -> WorkflowContext | None:
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

        `cpg_adapter` (W5 ADR-076) is injected by the composition root to allow
        task handlers to consume the CPG via the adapter port (fail-fast, no
        path-guessing). If not provided, the workflow feature never builds it.
        """
        if not chain_dir:
            return None

        workflow_path = _find_workflow_path()
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

                prompt_manager = PromptManager(
                    chain_dir=chain_dir, ckg_searcher=ckg_searcher
                )

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
                cpg_adapter=cpg_adapter,
            )
        except Exception as e:
            logger.warning(
                "WorkflowContext.resolve(%s) failed (%s: %s)",
                chain_dir, type(e).__name__, e,
            )
            return None


def _find_workflow_path() -> str | None:
    """Ruta del workflow GLOBAL del motor — delega en PathManager (SSOT único).

    MODELO (project.md § 'Configuración del workflow: GLOBAL del motor'):
    el workflow es configuración global del motor, compartida por todos los
    agentes. Los proyectos NO llevan su propio workflow.json — NO se busca
    nada dentro del chain_dir. `chain_dir` identifica el proyecto para
    ESTADO y MEMORIA (CPG/CKG/DeltaGraph/TraceGraph + state), nunca para
    cargar el flujo.

    Política (shared_kernel.path_manager.PathManager.resolve_workflow_manifest):
      1. Override explícito: env WALKER_WORKFLOW_MANIFEST / config
         'workflow.manifest' (absoluto tal cual; relativo → walker_root).
         Si no existe, el loader falla ruidosamente (fail-fast INV-016).
      2. Canónico: <walker_root>/data/workflow/workflow.json.
      3. None (caller decide el error accionable).
    """
    from fast_mcp.shared_kernel.path_manager import PathManager

    resolved = PathManager().resolve_workflow_manifest()
    return str(resolved) if resolved else None
