"""Knowledge status side-effect: update_knowledge_status.

Thin adapter: delega la orquestación cross-feature (Delta × CKG) en
KnowledgeStatusService (orchestration/).
"""
from __future__ import annotations
from typing import Any
from shared.common.shared_domain import ExecutionContext
from shared.core.engine.domain.side_effect_contract import as_side_effect, SCOPE_TRACKING


@as_side_effect(scope=SCOPE_TRACKING)
def update_knowledge_status(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Thin adapter: delega en KnowledgeStatusService."""
    from orchestration.knowledge_status_service import KnowledgeStatusService

    return KnowledgeStatusService.update_knowledge_status(
        chain_dir=ctx.chain_dir or "",
        event=params.get("event", ""),
    )


def register(engine: Any) -> None:
    engine.register("update_knowledge_status", update_knowledge_status)
