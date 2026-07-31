"""Side-effect: update_ckg_status — actualiza CKG status en sprint-state.

Se invoca desde nodos del workflow para tracking cross-module:
  - IMPLEMENT.WORK → pending_update
  - save_to_ckg / collect_knowledge → up_to_date
"""
from __future__ import annotations
import json
import os
from typing import Any
from features.engine.domain.models import ExecutionContext
from features.cognitive_knowledge.domain.services import CkgStatusUpdater


def update_ckg_status(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Side-effect handler: updates CKG status in sprint-state via SprintStateRepository (SSOT).

    Params:
        event: 'implementation_start' | 'knowledge_captured' | 'code_indexed'
    """
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return {"ok": False, "error": "chain_dir not available"}

    from features.spring.infrastructure.repositories import SprintStateRepository

    repo = SprintStateRepository()
    try:
        state = repo.load(chain_dir)
    except Exception as e:
        return {"ok": False, "error": f"sprint-state.json: {e}"}
    if not state.current_node and not state.context:
        return {"ok": False, "error": "sprint-state.json empty or not found"}

    current = state.context.get("ckg_status", "up_to_date")
    event = params.get("event", "")

    try:
        if event == "implementation_start":
            new_status = CkgStatusUpdater.on_implementation_start(current)
        elif event == "knowledge_captured":
            new_status = CkgStatusUpdater.on_knowledge_captured(current)
        elif event == "code_indexed":
            new_status = CkgStatusUpdater.on_code_indexed(current)
        else:
            return {"ok": False, "error": f"Unknown event '{event}'"}

        # SSOT: sprint-state lo escribe el store (unico dueno)
        repo.save_context(chain_dir, {"ckg_status": new_status})

        return {"ok": True, "previous_status": current, "status": new_status}

    except ValueError as e:
        return {"ok": False, "error": str(e), "current_status": current}


def register(engine: Any) -> None:
    engine.register("update_ckg_status", update_ckg_status)
