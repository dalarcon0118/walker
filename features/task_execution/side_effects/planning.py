"""Sprint planning side-effects: save_task_spec, advance_task."""
from __future__ import annotations
import json
import os
from typing import Any
from libs.shared_domain import ExecutionContext
from features.engine.domain.side_effect_contract import as_side_effect, SCOPE_TRACKING


@as_side_effect(scope=SCOPE_TRACKING)
def save_task_spec(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    sprint_dir = ctx.chain_dir or ""
    try:
        from features.delta_tracking.application.spring_graph_service import SpringGraphService
        spring_svc = SpringGraphService.from_chain_dir(sprint_dir)
        if not spring_svc:
            return {"ok": False, "error": "cannot resolve SpringGraphService"}
        spring_id = spring_svc.get_spring_id(sprint_dir)
        spring = spring_svc.ensure_spring_imported(sprint_dir, spring_id)
        if not spring:
            return {"ok": False, "error": "spring not found in delta"}
        current_idx = params.get("current_task_index", 0)
        specs = spring_svc.get_specs(spring_id)
        if current_idx >= len(specs):
            return {"ok": False, "error": f"Index {current_idx} fuera de rango"}
        target = specs[current_idx]
        spec_id = target.get("spec_id", f"SPEC-{current_idx + 1:03d}")
        # Refinar la spec en el delta (fuente unica)
        spring_svc.update_spec(spring_id, spec_id, {
            "objective": params.get("objective", target.get("objective", "")),
            "type": params.get("type", target.get("spec_type", "feature")),
            "domain": params.get("domain", target.get("domain", "")),
            "result_state": params.get("result_state", ""),
            "task_description": params.get("task_description", ""),
            "predicates": params.get("predicates", []),
            "files": params.get("files", []),
            "status": "specified",
        })
        return {"ok": True, "task_id": spec_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@as_side_effect(scope=SCOPE_TRACKING)
def advance_task(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    sprint_dir = ctx.chain_dir or ""
    from features.delta_tracking.application.spring_graph_service import SpringGraphService

    spring_svc = SpringGraphService.from_chain_dir(sprint_dir)
    if not spring_svc:
        return {"ok": False, "error": "cannot resolve SpringGraphService"}
    spring_id = spring_svc.get_spring_id(sprint_dir)

    # ── Resolver total de specs ANTES de avanzar (para clamp) ──
    total_specs = 0
    specs = []
    try:
        spring_svc.ensure_spring_imported(sprint_dir, spring_id)
        specs = spring_svc.get_specs_legacy(spring_id)
    except Exception:
        specs = []
    if not specs:
        # Fallback legacy: plan.json
        plan_path = os.path.join(sprint_dir, "plan.json")
        try:
            with open(plan_path) as f: plan = json.load(f)
            specs = plan.get("specifications", plan.get("tasks", []))
        except Exception:
            specs = []
    total_specs = len(specs)

    # ── Contexto actual desde el DELTA (nodo spring) ──
    ctx_state = spring_svc.get_spring_context(spring_id)
    next_idx = ctx_state.get("current_task_index", 0) + 1
    # SSOT: clamp al rango de specs. Si all_done (ultima spec), el indice
    # queda en el ultimo valido, no crece indefinidamente.
    current_idx = min(next_idx, max(total_specs - 1, 0)) if total_specs > 0 else next_idx
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
        return {"ok": True, "current_task_index": current_idx,
                "next_spec_id": next_spec_id, "all_done": all_done}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def register(engine: Any) -> None:
    engine.register("save_task_spec", save_task_spec)
    engine.register("advance_task", advance_task)
