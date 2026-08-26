"""Decision side-effect: create_adr.

Crea ADR en CKG desde Spec aprobada como decision.
Orquesta Delta × CKG × Project vía AdrOrchestratorService (ADR-051 §2.5).
"""
from __future__ import annotations

import logging
from typing import Any

from shared.core.engine.domain.side_effect_contract import as_side_effect, SCOPE_KNOWLEDGE

logger = logging.getLogger(__name__)


def _resolve_ref(chain_dir: str, ref: str) -> Any:
    """Resuelve una referencia NODE.SUB.output.field desde sub_state_data."""
    if not ref or not isinstance(ref, str) or not ref.startswith("NODE."):
        return ref
    parts = ref.split(".")
    if len(parts) < 4 or parts[2] != "output":
        return ref
    sub_state_key = parts[1]
    field = ".".join(parts[3:])
    try:
        from shared.core.engine.infrastructure.repositories import WorkflowStateRepository

        state = WorkflowStateRepository().load(chain_dir)
        prefixed = ""
        if state.current_node:
            prefixed = f"{state.current_node}.{sub_state_key}"
        ss_data = state.sub_state_data.get(prefixed) or state.sub_state_data.get(sub_state_key)
        if ss_data:
            val = ss_data.fields.get(field)
            if val is not None:
                return val
    except Exception:
        pass
    return ref


@as_side_effect(scope=SCOPE_KNOWLEDGE)
def create_adr(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Thin adapter: delega la orquestación cross-feature en AdrOrchestratorService."""
    chain_dir = getattr(ctx, "chain_dir", "") or ""
    if not chain_dir:
        return {"ok": False, "error": "chain_dir not available"}

    approval_type_ref = params.get("approval_type", "standard")
    approval_type = _resolve_ref(chain_dir, approval_type_ref) if isinstance(approval_type_ref, str) else approval_type_ref
    if not isinstance(approval_type, str):
        approval_type = "standard"

    from orchestration.adr_orchestrator_service import AdrOrchestratorService
    return AdrOrchestratorService.create_adr_from_spec(
        chain_dir=chain_dir,
        approval_type=approval_type,
    )


def register(engine: Any) -> None:
    engine.register("create_adr", create_adr)
