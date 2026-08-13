"""side_effects/create_adr.py — Side-effect: crea ADR en CKG desde Spec aprobada como decision.

MECANISMO: cuando TASK_REVIEW aprueba una Spec con approval_type=decision,
este side-effect:
  1. Lee la Spec aprobada desde DeltaGraph
  2. Crea un ADR en CKG con generated_from=SPEC-NNN
  3. Actualiza la Spec con related_decisions=[ADR-WALKER-NNN]
  4. Emite evento decision_approved en Trace

CICLO DE VIDA: si el ADR ya existe (idempotencia), no lo duplica.
"""
from __future__ import annotations

import logging
from typing import Any

from core.engine.domain.side_effect_contract import as_side_effect, SCOPE_KNOWLEDGE

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
    except Exception:
        pass
    return ref


def _get_current_spec_id(chain_dir: str) -> str | None:
    try:
        from features.delta_tracking import spring_service as _spring_service
        spring_svc = _spring_service(chain_dir=chain_dir)
        if spring_svc is None:
            return None
        spring_id = spring_svc.get_spring_id(chain_dir)
        specs = spring_svc.get_specs_legacy(spring_id)
        if not specs:
            return None
        current = specs[-1] if isinstance(specs, list) else next(iter(specs.values()))
        if isinstance(current, dict):
            return current.get("spec_id")
        return getattr(current, "spec_id", None)
    except Exception:
        return None


@as_side_effect(scope=SCOPE_KNOWLEDGE)
def create_adr_from_spec(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    chain_dir = getattr(ctx, "chain_dir", "") or ""
    if not chain_dir:
        return {"ok": False, "error": "chain_dir not available"}

    approval_type_ref = params.get("approval_type", "standard")
    approval_type = _resolve_ref(chain_dir, approval_type_ref) if isinstance(approval_type_ref, str) else approval_type_ref
    if not isinstance(approval_type, str):
        approval_type = "standard"

    if approval_type != "decision":
        return {
            "ok": True,
            "skipped": True,
            "reason": f"approval_type={approval_type}, no es decision",
        }

    spec_id = _get_current_spec_id(chain_dir)
    if not spec_id:
        return {"ok": False, "error": "no current spec found"}

    try:
        from features.delta_tracking import spring_service as _spring_service
        from features.cognitive_knowledge import ckg_service
        from features.project import ProjectService

        spring_svc = _spring_service(chain_dir=chain_dir)
        if spring_svc is None:
            return {"ok": False, "error": "Spring service not available"}

        spring_id = spring_svc.get_spring_id(chain_dir)
        specs = spring_svc.get_specs_legacy(spring_id)
        current_spec = None
        if specs:
            current_spec = specs[-1] if isinstance(specs, list) else next(iter(specs.values()))

        if not current_spec:
            return {"ok": False, "error": "Spec not found in spring"}

        spec_dict = current_spec if isinstance(current_spec, dict) else current_spec.to_dict()
        task_id = spec_dict.get("task_id", spec_id)
        title = spec_dict.get("task_description", spec_dict.get("result_state", task_id))[:120]

        paths = ProjectService.resolve_paths(chain_dir)
        project_root = str(paths.project_root)
        ckg = ckg_service(project_root)

        existing_adrs = ckg.query({"type": "decision", "metadata.spec_id": spec_id})
        if existing_adrs:
            adr_id = existing_adrs[0].get("_id", "")
            related = [adr_id]
            existing_related = spec_dict.get("related_decisions", [])
            if isinstance(existing_related, list):
                related = existing_related + [adr_id] if adr_id not in existing_related else existing_related
            updates = {
                "related_decisions": related,
                "generated_from": f"SPEC:{spec_id}",
            }
            spring_svc.update_spec(spring_id, spec_id, updates)
            return {
                "ok": True,
                "type": "create_adr_from_spec",
                "spec_id": spec_id,
                "adr_id": adr_id,
                "related_decisions": related,
                "generated_from": f"SPEC:{spec_id}",
                "idempotent": True,
            }

        adr_id = ckg._next_id("ADR")
        adr_node = {
            "type": "decision",
            "label": f"ADR: {title}",
            "statement": f"ADR-{adr_id}: {title}",
            "description": (
                f"Decision arquitectonica generada desde Spec {spec_id} "
                f"(task {task_id}). Aprobada en TASK_REVIEW."
            ),
            "metadata": {
                "task_id": task_id,
                "spec_id": spec_id,
                "generated_from": f"SPEC:{spec_id}",
                "source": f"session:{getattr(ctx, 'session_id', '')}",
                "approval_type": "decision",
            },
            "supersedes": [],
        }

        save_result = ckg.save_nodes([adr_node])
        node_ids = save_result.get("node_ids", [])
        adr_id = node_ids[0] if node_ids else adr_id

        related = [adr_id]
        existing_related = spec_dict.get("related_decisions", [])
        if isinstance(existing_related, list):
            related = existing_related + [adr_id]

        generated_from = f"SPEC:{spec_id}"
        updates = {
            "related_decisions": related,
            "generated_from": generated_from,
        }
        spring_svc.update_spec(spring_id, spec_id, updates)

        return {
            "ok": True,
            "type": "create_adr_from_spec",
            "spec_id": spec_id,
            "adr_id": adr_id,
            "related_decisions": related,
            "generated_from": generated_from,
        }
    except Exception as e:
        logger.warning("create_adr_from_spec fallo (%s: %s)", type(e).__name__, e)
        return {"ok": False, "error": str(e)}


def register(engine: Any) -> None:
    engine.register("create_adr_from_spec", create_adr_from_spec)
