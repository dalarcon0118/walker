"""use_cases/specify_spec.py — SpecifySpecUseCase.

Crea o refina una especificación en el delta (fuente unica).
Called by spec_task workflow action.
"""
from __future__ import annotations

from shared.core.engine.domain.execution import ExecutionContext
from ...domain.services.spec_service import (
    SpecValidator,
    SpecBuilder,
)
from ._sprint_helpers import _spring_svc, _get_current_spec_index


class SpecifySpecUseCase:
    """Crea o refina una especificación en el delta (fuente unica)."""

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        if not sprint_dir:
            raise ValueError("chain_dir not available")

        # Fuente unica: delta (SpringService). No hay plan.json paralelo.
        spring_svc = _spring_svc(sprint_dir)
        spring_id = spring_svc.get_spring_id(sprint_dir)
        spring_svc.ensure_spring_imported(sprint_dir, spring_id)
        specs = spring_svc.get_specs_legacy(spring_id)

        # Phase 1: Create initial spec if plan is empty
        is_initial = data.get("spec_id", data.get("task_id")) and not data.get(
            "result_state"
        )
        if not specs and is_initial:
            new_spec = SpecBuilder.create_default(0, data)
            spring_svc.add_spec(
                spring_id,
                objective=new_spec.get("objective", ""),
                domain=new_spec.get("domain", ""),
                spec_type=new_spec.get("type", "feature"),
                spec_index=0,
                status="draft",
            )
            # SSOT: el contexto de negocio vive en el delta (nodo spring)
            spring_svc.set_spring_context(
                spring_id, context={"current_spec_index": 0, "current_task_index": 0}
            )
            return {
                "spec_id": new_spec["spec_id"],
                "status": "draft",
                "message": f"Spec {new_spec['spec_id']} created.",
            }

        # Phase 2: Get current index
        current_idx = _get_current_spec_index(sprint_dir)
        if not specs:
            raise ValueError("No specifications in spring; use plan_sprint first.")
        current_idx = min(current_idx, len(specs) - 1)
        current_spec = specs[current_idx]

        # Phase 3: No new data → return current state
        has_data = any(
            k in data
            for k in (
                "result_state",
                "task_description",
                "predicates",
                "files",
                "preconditions",
                "acceptance_criteria",
                "quality_gates",
            )
        )
        if not has_data:
            return {
                "spec_id": current_spec.get("spec_id", current_spec.get("task_id", "")),
                "status": current_spec.get("status", "draft"),
                "current_index": current_idx,
                "total_specs": len(specs),
            }

        # Phase 4: Validate + refine
        errors = SpecValidator.validate_fields(data)
        if errors:
            raise ValueError(f"Required fields missing: {'; '.join(errors)}")

        refined = SpecBuilder.refine(current_spec, data)
        specs[current_idx] = refined
        spec_id = refined.get(
            "spec_id", refined.get("task_id", f"SPEC-{current_idx + 1:03d}")
        )
        spring_svc.update_spec(spring_id, spec_id, refined)

        # ── Phase 5: Validar la spec COMPLETA antes de permitir avanzar ──
        # Regla del workflow: el sprint solo avanza de TASK_SPEC si la spec
        # actual pasa la validacion completa (7 campos required + status
        # 'specified'). Si no, sigue en especificacion/refinamiento.
        spec_errors = SpecValidator.validate_spec(refined)
        status = refined.get("status", "draft")
        if spec_errors:
            return {
                "spec_id": refined["spec_id"],
                "status": status,
                "valid": False,
                "errors": spec_errors,
                "current_index": current_idx,
                "total_specs": len(specs),
                "message": f"Spec {refined['spec_id']} incomplete: "
                f"{len(spec_errors)} error(es). Sigue en TASK_SPEC.",
            }

        return {
            "spec_id": refined["spec_id"],
            "status": status,
            "valid": True,
            "current_index": current_idx,
            "total_specs": len(specs),
        }
