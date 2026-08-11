"""side_effects/spec_transform.py — Side effect para transformar Specs en el workflow.

Registra el handler `transform_spec` en SideEffectEngine.
Este handler aplica las transformaciones del EDM (Evolutionary Document Model)
a la Spec según el nodo actual del workflow.
"""
from __future__ import annotations

import logging
from typing import Any

from features.delta_tracking.domain.entities import Spec

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
        from features.engine.infrastructure.repositories import WorkflowStateRepository
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


def _resolve_fields(chain_dir: str, fields: dict) -> dict:
    resolved = {}
    for key, value in fields.items():
        if isinstance(value, str) and value.startswith("NODE."):
            resolved_value = _resolve_ref(chain_dir, value)
            if isinstance(resolved_value, dict) and key in resolved_value:
                resolved[key] = resolved_value[key]
            else:
                resolved[key] = resolved_value
        else:
            resolved[key] = value
    return resolved


def transform_spec(ctx: Any, params: dict) -> dict:
    """Transforma una Spec aplicando campos del EDM para el nodo actual.

    Args:
        ctx: ExecutionContext con chain_dir, sprint_id, etc.
        params: Dict con:
            - status: str (draft|specified|approved|in_progress|in_review|completed)
            - fields: dict con campos a aplicar en este nodo
            - optional: bool (opcional, default False)

    Returns:
        dict con ok, spec_id, status, fields_applied, message
    """
    try:
        status = params.get("status", "")
        fields = params.get("fields", {})
        optional = params.get("optional", False)

        if not status:
            if optional:
                return {"ok": True, "skipped": True, "reason": "no status provided"}
            return {"ok": False, "error": "status required for transform_spec"}

        sprint_dir = getattr(ctx, "chain_dir", "") or ""
        if not sprint_dir:
            if optional:
                return {"ok": True, "skipped": True, "reason": "no chain_dir"}
            return {"ok": False, "error": "chain_dir not available in context"}

        from features.delta_tracking import spring_service as _spring_service
        from features.delta_tracking.application.evolutionary_document_model import (
            EvolutionaryDocumentModel,
        )
        from features.delta_tracking.application.memory_orchestrator import (
            MemoryOrchestrator,
        )

        spring_svc = _spring_service(chain_dir=sprint_dir)
        if spring_svc is None:
            return {"ok": False, "error": "Spring service not available"}

        spring_id = spring_svc.get_spring_id(sprint_dir)
        specs = spring_svc.get_specs_legacy(spring_id)
        if not specs:
            return {"ok": False, "error": "No specs found in spring"}

        current_spec = specs[-1] if isinstance(specs, list) else next(iter(specs.values()))
        spec_obj = current_spec if isinstance(current_spec, Spec) else Spec.from_dict(current_spec)
        spec_id = spec_obj.spec_id

        node_key = ""
        if hasattr(ctx, "baggage") and isinstance(ctx.baggage, dict):
            node_key = ctx.baggage.get("node_key", "")

        resolved_fields = _resolve_fields(sprint_dir, fields)

        edm = EvolutionaryDocumentModel(chain_dir=sprint_dir)
        orchestrator = MemoryOrchestrator(
            spring_service=spring_svc,
            trace_port=getattr(ctx, "trace_port", None),
        )
        transformed, results = edm.transform(
            spec_obj, node_key, resolved_fields,
            memory_orchestrator=orchestrator,
            context={"spring_id": spring_id, "chain_dir": sprint_dir},
        )

        if transformed.status == spec_obj.status and not resolved_fields:
            if optional:
                return {"ok": True, "skipped": True, "reason": "no changes"}
            return {"ok": False, "error": "transform_spec did not change spec"}

        saved = spring_svc.update_spec(spring_id, spec_id, transformed.to_dict())
        if not saved:
            return {"ok": False, "error": f"Failed to persist Spec {spec_id}"}

        return {
            "ok": True,
            "type": "transform_spec",
            "spec_id": spec_id,
            "status": transformed.status,
            "fields_applied": list(resolved_fields.keys()),
            "memory_ops_results": results,
            "result": {"transformed": True, "saved": True},
        }

    except Exception as e:
        logger.warning("transform_spec fallo (%s: %s)", type(e).__name__, e)
        return {"ok": False, "error": str(e)}


def register(engine: Any) -> None:
    engine.register("transform_spec", transform_spec)
