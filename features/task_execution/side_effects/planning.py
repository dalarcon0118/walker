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
    from features.spring.infrastructure.repositories import SprintStateRepository

    repo = SprintStateRepository()
    try:
        state = repo.load(sprint_dir)
    except Exception as e:
        return {"ok": False, "error": f"sprint-state.json: {e}"}
    if not state.current_node and not state.context:
        return {"ok": False, "error": "sprint-state.json empty or not found"}

    # ── Resolver total de specs ANTES de avanzar (para clamp) ──
    total_specs = 0
    specs = []
    try:
        from features.delta_tracking.application.spring_graph_service import SpringGraphService
        spring_svc = SpringGraphService.from_chain_dir(sprint_dir)
        if spring_svc:
            spring_id = spring_svc.get_spring_id(sprint_dir)
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

    next_idx = state.context.get("current_task_index", 0) + 1
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
        if not all_done:
            next_spec_id = specs[current_idx + 1].get("spec_id", specs[current_idx + 1].get("task_id", ""))
        else:
            # En la ultima spec: el task_id actual es esa spec
            next_spec_id = specs[current_idx].get("spec_id", specs[current_idx].get("task_id", ""))
    # ── task_id de la siguiente spec: vive en SPRINT-STATE (negocio) ──
    # PRINCIPIO: spring actualiza sprint-state.json. El workflow lee el
    # task_id desde aqui (WorkflowAPI._build_context). El workflow-state
    # NO debe duplicar datos de negocio.
    if next_spec_id:
        context_updates["task_id"] = next_spec_id
    try:
        # SSOT: sprint-state lo escribe el store (unico dueno)
        repo.save_context(sprint_dir, context_updates)
        return {"ok": True, "current_task_index": current_idx,
                "next_spec_id": next_spec_id, "all_done": all_done}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def register(engine: Any) -> None:
    engine.register("save_task_spec", save_task_spec)
    engine.register("advance_task", advance_task)
