"""Side-effect: update_ckg_status — actualiza CKG status en sprint-state.

Se invoca desde nodos del workflow para tracking cross-module:
  - IMPLEMENT.WORK → pending_update
  - save_to_ckg / collect_knowledge → up_to_date
"""

from __future__ import annotations
import os
from typing import Any
from features.engine.domain.models import ExecutionContext
from features.engine.domain.side_effect_contract import as_side_effect, SCOPE_TRACKING
from features.cognitive_knowledge.domain.services import CkgStatusUpdater


@as_side_effect(scope=SCOPE_TRACKING)
def update_ckg_status(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Side-effect handler: actualiza ckg_status en el nodo spring del delta.

    Params:
        event: 'implementation_start' | 'knowledge_captured' | 'code_indexed'
    """
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return {"ok": False, "error": "chain_dir not available"}

    try:
        from features.delta_tracking.application.spring_graph_service import (
            SpringGraphService,
        )

        spring_svc = SpringGraphService.from_chain_dir(chain_dir)
        if not spring_svc:
            return {"ok": False, "error": "cannot resolve SpringGraphService"}
        spring_id = spring_svc.get_spring_id(chain_dir)
        spring = spring_svc.ensure_spring_imported(chain_dir, spring_id)
        if not spring:
            return {"ok": False, "error": "spring not found in delta"}

        current = spring.get("ckg_status", "up_to_date")
        event = params.get("event", "")

        if event == "implementation_start":
            new_status = CkgStatusUpdater.on_implementation_start(current)
        elif event == "knowledge_captured":
            new_status = CkgStatusUpdater.on_knowledge_captured(current)
        elif event == "code_indexed":
            new_status = CkgStatusUpdater.on_code_indexed(current)
        else:
            return {"ok": False, "error": f"Unknown event '{event}'"}

        # Fuente unica: delta — actualizar ckg_status en el nodo spring
        data = dict(spring)
        data["ckg_status"] = new_status
        spring_svc._store(spring_id, data)

        return {"ok": True, "previous_status": current, "status": new_status}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def register(engine: Any) -> None:
    engine.register("update_ckg_status", update_ckg_status)
