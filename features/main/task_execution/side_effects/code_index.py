"""Code index side-effects: index_code, build_project_graph, sync_delta.

Thin adapters: delegan la orquestación cross-feature (Project × CPG) en
CodeIndexOrchestratorService (orchestration/).
"""
from __future__ import annotations
from typing import Any
from shared.common.shared_domain import ExecutionContext
from core.engine.domain.side_effect_contract import as_side_effect, SCOPE_CODE_INDEX


@as_side_effect(scope=SCOPE_CODE_INDEX)
def index_code(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Thin adapter: delega en CodeIndexOrchestratorService."""
    from orchestration.code_index_orchestrator_service import CodeIndexOrchestratorService

    return CodeIndexOrchestratorService.index_code(
        chain_dir=ctx.chain_dir or "",
        task_id=ctx.task_id or "",
        scope=params.get("scope", "incremental"),
        ctx=ctx,
    )


@as_side_effect(scope=SCOPE_CODE_INDEX)
def build_project_graph(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Thin adapter: delega en CodeIndexOrchestratorService (scope=full)."""
    from orchestration.code_index_orchestrator_service import CodeIndexOrchestratorService

    return CodeIndexOrchestratorService.index_code(
        chain_dir=ctx.chain_dir or "",
        task_id=ctx.task_id or "",
        scope="full",
        ctx=ctx,
    )


@as_side_effect(scope=SCOPE_CODE_INDEX)
def sync_delta(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Thin adapter: delega en CodeIndexOrchestratorService."""
    from orchestration.code_index_orchestrator_service import CodeIndexOrchestratorService

    return CodeIndexOrchestratorService.sync_delta(
        chain_dir=ctx.chain_dir or "",
        task_id=ctx.task_id or "",
        ctx=ctx,
    )


def register(engine: Any) -> None:
    engine.register("index_code", index_code)
    engine.register("build_project_graph", build_project_graph)
    engine.register("sync_delta", sync_delta)
