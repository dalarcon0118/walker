"""Archive side-effects: archive_sprint, restore_cpg_snapshot."""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext
from features.engine.domain.side_effect_contract import as_side_effect, SCOPE_SNAPSHOT


@as_side_effect(scope=SCOPE_SNAPSHOT)
def archive_sprint(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "archived": True, "sprint_id": ctx.session_id}


@as_side_effect(scope=SCOPE_SNAPSHOT)
def restore_cpg_snapshot(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "restored": True}


def register(engine: Any) -> None:
    engine.register("archive_sprint", archive_sprint)
    engine.register("restore_cpg_snapshot", restore_cpg_snapshot)
