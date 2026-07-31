"""Sprint planning side-effects: save_task_spec, advance_task."""
from __future__ import annotations
import json
import os
from typing import Any
from libs.shared_domain import ExecutionContext


def save_task_spec(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    sprint_dir = ctx.chain_dir or ""
    plan_path = os.path.join(sprint_dir, "plan.json")
    if not os.path.exists(plan_path):
        return {"ok": False, "error": "plan.json not found"}
    try:
        with open(plan_path) as f: plan = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return {"ok": False, "error": str(e)}
    tasks = plan.get("tasks", [])
    current_idx = params.get("current_task_index", 0)
    if current_idx >= len(tasks):
        return {"ok": False, "error": f"Index {current_idx} fuera de rango"}
    tasks[current_idx] = {"task_id": tasks[current_idx].get("task_id", f"TASK-{current_idx+1:03d}"),
                          "objective": params.get("objective", tasks[current_idx].get("objective", "")),
                          "type": params.get("type", tasks[current_idx].get("type", "feature")),
                          "domain": params.get("domain", tasks[current_idx].get("domain", "")),
                          "result_state": params.get("result_state", ""),
                          "task_description": params.get("task_description", ""),
                          "validation": {"predicates": params.get("predicates", [])},
                          "impacted_artifacts": {"files": params.get("files", [])},
                          "depends_on": params.get("depends_on", []),
                          "risks": {"curvature": float(params.get("curvature", 0.3))}, "status": "specified"}
    try:
        with open(plan_path, "w") as f: json.dump(plan, f, indent=2, ensure_ascii=False)
        return {"ok": True, "task_id": tasks[current_idx]["task_id"]}
    except OSError as e:
        return {"ok": False, "error": str(e)}


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
    current_idx = state.context.get("current_task_index", 0) + 1
    # SSOT: mantener sincronizados ambos indices para que
    # _get_current_spec_index (que prioriza current_spec_index) apunte
    # a la siguiente spec.
    context_updates = {
        "current_task_index": current_idx,
        "current_spec_index": current_idx,
    }
    all_done = False
    plan_path = os.path.join(sprint_dir, "plan.json")
    next_spec_id = ""
    if os.path.exists(plan_path):
        try:
            with open(plan_path) as f: plan = json.load(f)
            # SSOT: plan.json usa "specifications" (no "tasks")
            specs = plan.get("specifications", plan.get("tasks", []))
            all_done = current_idx >= len(specs)
            if not all_done and current_idx < len(specs):
                next_spec_id = specs[current_idx].get("spec_id", specs[current_idx].get("task_id", ""))
        except Exception: pass
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
