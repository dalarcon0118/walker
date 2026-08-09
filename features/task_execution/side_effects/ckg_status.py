"""Side-effect: update_ckg_status — actualiza el status CKG en el nodo spring del delta.

Refactorizado para usar Result pattern (dry-python/returns).
"""

from __future__ import annotations
from typing import Any

from returns.result import safe
from returns.pipeline import is_successful

from features.engine.domain.models import ExecutionContext
from features.engine.domain.side_effect_contract import as_side_effect, SCOPE_TRACKING
from features.cognitive_knowledge.domain.services import CkgStatusUpdater


@as_side_effect(scope=SCOPE_TRACKING)
def update_ckg_status(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Side-effect handler: actualiza ckg_status en el nodo spring del delta."""
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return {"ok": False, "error": "chain_dir not available"}

    @safe
    def _update() -> dict:
        from features.delta_tracking import SpringService, spring_service

        spring_svc = spring_service(chain_dir=chain_dir)
        if not spring_svc:
            raise RuntimeError("cannot resolve SpringService")

        spring_id = spring_svc.get_spring_id(chain_dir)
        spring = spring_svc.ensure_spring_imported(chain_dir, spring_id)
        if not spring:
            raise RuntimeError("spring not found in delta")

        current = spring.get("ckg_status", "up_to_date")
        event = params.get("event", "")

        if event == "implementation_start":
            new_status = CkgStatusUpdater.on_implementation_start(current)
        elif event == "knowledge_captured":
            new_status = CkgStatusUpdater.on_knowledge_captured(current)
        elif event == "code_indexed":
            new_status = CkgStatusUpdater.on_code_indexed(current)
        else:
            raise ValueError(f"Unknown event '{event}'")

        # SSOT #4: escritura via port (save_spring) — nunca _store raw.
        spring_svc.update_spring(spring_id, {"ckg_status": new_status})

        return {"ok": True, "previous_status": current, "status": new_status}

    result = _update()
    if is_successful(result):
        return result.value_or({})
    else:
        return {"ok": False, "error": str(result.failure())}


def register(engine: Any) -> None:
    engine.register("update_ckg_status", update_ckg_status)
