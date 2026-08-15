"""Sprint planning side-effects: advance_task.

SSOT de main (SPEC-014 Fase 0): el handler advance_task vive AQUÍ, migrado
desde el paquete huérfano features/delta_tracking/side_effects (que se
elimina). save_task_spec NO es side effect: es una TASK (tasks/planning.py,
alias de spec_task) — no se registra en el engine.

Result pattern (convention-001): ante fallo se degrada con fallback explícito
+ log; nunca except silencioso.
"""

from __future__ import annotations
import logging
from typing import Any
from shared.common.shared_domain import ExecutionContext
from core.engine.domain.side_effect_contract import as_side_effect, SCOPE_TRACKING

logger = logging.getLogger(__name__)


@as_side_effect(scope=SCOPE_TRACKING)
def advance_sprint_task(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    sprint_dir = ctx.chain_dir or ""
    from features.delta_tracking import spring_service

    spring_svc = spring_service(chain_dir=sprint_dir)
    if not spring_svc:
        return {"ok": False, "error": "cannot resolve SpringService"}
    spring_id = spring_svc.get_spring_id(sprint_dir)

    # ── Resolver total de specs ANTES de avanzar (para clamp) ──
    total_specs = 0
    specs = []
    try:
        spring_svc.ensure_spring_imported(sprint_dir, spring_id)
        specs = spring_svc.get_specs_legacy(spring_id)
    except Exception as e:
        logger.warning(
            "planning: advance_sprint_task no pudo cargar specs (%s: %s); specs vacías",
            type(e).__name__,
            e,
        )
        specs = []
    # Fuente unica: delta. No hay fallback a plan.json (ADR-WALKER-013).
    total_specs = len(specs)

    # ── Contexto actual desde el DELTA (nodo spring) ──
    ctx_state = spring_svc.get_spring_context(spring_id)
    next_idx = ctx_state.get("current_task_index", 0) + 1
    # SSOT: clamp al rango de specs. Si all_done (ultima spec), el indice
    # queda en el ultimo valido, no crece indefinidamente.
    current_idx = (
        min(next_idx, max(total_specs - 1, 0)) if total_specs > 0 else next_idx
    )
    context_updates = {
        "current_task_index": current_idx,
        "current_spec_index": current_idx,
    }
    all_done = current_idx >= total_specs - 1 if total_specs > 0 else False
    next_spec_id = ""
    if total_specs > 0:
        # current_idx YA es el indice de la siguiente tarea a especificar
        # (next_idx = current_task_index + 1). Usar specs[current_idx] sin
        # sumar +1 evita el off-by-one que adelantaba una tarea.
        next_spec_id = specs[current_idx].get(
            "spec_id", specs[current_idx].get("task_id", "")
        )
    # ── task_id de la siguiente spec: vive en el DELTA (nodo spring) ──
    # PRINCIPIO: el estado de negocio del spring vive en el nodo spring
    # del delta. El workflow lee el task_id desde aqui
    # (WorkflowAPI._build_context). .workflow-state.json NO duplica
    # datos de negocio. sprint-state.json fue eliminado.
    if next_spec_id:
        context_updates["task_id"] = next_spec_id
    try:
        spring_svc.set_spring_context(spring_id, **context_updates)
        return {
            "ok": True,
            "current_task_index": current_idx,
            "next_spec_id": next_spec_id,
            "all_done": all_done,
        }
    except Exception as e:
        logger.warning(
            "planning: set_spring_context fallo (%s: %s)",
            type(e).__name__,
            e,
        )
        return {"ok": False, "error": str(e)}


def register(engine: Any) -> None:
    engine.register("advance_sprint_task", advance_sprint_task)
