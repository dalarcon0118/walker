"""side_effects/spec_transform.py — Side effect para transformar Specs en el workflow.

Registra el handler `transform_spec` en SideEffectEngine.
Este handler aplica las transformaciones del EDM (Evolutionary Document Model)
a la Spec según el nodo actual del workflow.
"""
from __future__ import annotations

import logging
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from features.delta_tracking.domain.entities import Spec

logger = logging.getLogger(__name__)


@safe
def _resolve_ref_from_state(chain_dir: str, sub_state_key: str, field: str) -> Any:
    """Core puro: lee el workflow state y resuelve NODE.SUB.output.field (puede lanzar)."""
    from core.engine.infrastructure.repositories import WorkflowStateRepository

    state = WorkflowStateRepository().load(chain_dir)
    prefixed = ""
    if state.current_node:
        prefixed = f"{state.current_node}.{sub_state_key}"
    ss_data = state.sub_state_data.get(prefixed) or state.sub_state_data.get(sub_state_key)
    if ss_data:
        val = ss_data.fields.get(field)
        if val is not None:
            return val
    return None


def _resolve_ref(chain_dir: str, ref: str) -> Any:
    """Resuelve una referencia NODE.SUB.output.field desde sub_state_data.

    Result pattern (convention-001): el core @safe nunca lanza. Ante fallo de
    resolución se devuelve la ref cruda — la validación posterior de
    transform_spec la rechaza con ok=False (SPEC-005 predicate 4).
    """
    if not ref or not isinstance(ref, str) or not ref.startswith("NODE."):
        return ref
    parts = ref.split(".")
    if len(parts) < 4 or parts[2] != "output":
        return ref
    outcome = _resolve_ref_from_state(chain_dir, parts[1], ".".join(parts[3:]))
    if not is_successful(outcome):
        logger.debug("_resolve_ref no resolvio '%s' (%s)", ref, outcome.failure())
    value = outcome.value_or(None)
    if value is not None:
        return value
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


def _find_unresolved_refs(fields: dict) -> list[str]:
    """Devuelve las claves cuyo valor sigue siendo una ref NODE. sin resolver.

    Recorre también dicts y listas anidados (rutas tipo 'a.b' y 'a[0]') para
    no persistir refs crudas en ningún nivel (SPEC-005 predicate 4).
    """
    unresolved: list[str] = []

    def _scan(value: Any, path: str) -> None:
        if isinstance(value, str):
            if value.startswith("NODE."):
                unresolved.append(path)
        elif isinstance(value, dict):
            for key, sub in value.items():
                _scan(sub, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for i, sub in enumerate(value):
                _scan(sub, f"{path}[{i}]")

    _scan(fields, "")
    return unresolved


@safe
def _transform_spec_core(ctx: Any, params: dict) -> dict:
    """Core puro de transform_spec: aplica campos del EDM (puede lanzar)."""
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

    # SPEC-005 predicate 4: si una ref NODE.*.output.field sigue sin
    # resolver, NO se persiste la ref cruda en el delta — el transform
    # falla con ok=False (el fallo es un dato roto, no un skip).
    unresolved_refs = _find_unresolved_refs(resolved_fields)
    if unresolved_refs:
        return {
            "ok": False,
            "error": (
                "transform_spec: refs NODE. sin resolver "
                f"(no se persiste ref cruda): {unresolved_refs}"
            ),
            "unresolved_refs": unresolved_refs,
        }

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


def transform_spec(ctx: Any, params: dict) -> dict:
    """Transforma una Spec aplicando campos del EDM para el nodo actual.

    Result pattern (convention-001): el core @safe nunca lanza; el wrapper
    traduce el Failure a {"ok": False} con log. Devuelve dict con ok,
    spec_id, status, fields_applied, message.
    """
    outcome = _transform_spec_core(ctx, params)
    if is_successful(outcome):
        return outcome.value_or({"ok": False, "error": "transform_spec: empty result"})
    error = outcome.failure()
    logger.warning("transform_spec fallo (%s: %s)", type(error).__name__, error)
    return {"ok": False, "error": str(error)}


def register(engine: Any) -> None:
    engine.register("transform_spec", transform_spec)
