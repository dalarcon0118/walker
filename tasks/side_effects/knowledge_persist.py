"""Knowledge persist side-effects: documenting_knowledge, remove_knowledge.

Thin adapters: delegan la orquestación cross-feature (Project × CKG) en
KnowledgePersistService (app/orchestration/).
"""
from __future__ import annotations
from typing import Any
from shared.core.engine.domain.execution import ExecutionContext
from shared.core.engine.domain.side_effect_contract import as_side_effect, SCOPE_KNOWLEDGE


@as_side_effect(scope=SCOPE_KNOWLEDGE)
def documenting_knowledge(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Thin adapter: delega en KnowledgePersistService."""
    from app.orchestration.knowledge_persist_service import KnowledgePersistService

    return KnowledgePersistService.documenting_knowledge(
        chain_dir=ctx.chain_dir or "",
        nodes=params.get("nodes", []),
        task_id=ctx.task_id or "",
        session_id=getattr(ctx, "session_id", ""),
    )


@as_side_effect(scope=SCOPE_KNOWLEDGE)
def remove_knowledge(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Compensación canónica: elimina nodos de conocimiento creados
    por documenting_knowledge durante el rollback de saga.

    Thin adapter: delega en KnowledgePersistService.
    """
    from app.orchestration.knowledge_persist_service import KnowledgePersistService

    return KnowledgePersistService.remove_knowledge(
        chain_dir=ctx.chain_dir or "",
        task_id=ctx.task_id or "",
    )


def register(engine: Any) -> None:
    engine.register("documenting_knowledge", documenting_knowledge)
    engine.register("remove_knowledge", remove_knowledge)
