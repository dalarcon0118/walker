"""collector.py — Recolecta datos para inyectar en templates."""

from __future__ import annotations
from libs.infrastructure import CognitiveGraphSearcher
from features.project import ProjectService


def collect_activity(chain_dir: str, node_id: str | None = None) -> dict:
    paths = ProjectService.resolve_paths(chain_dir)
    cg = CognitiveGraphSearcher(paths.project_root)
    activity = {"id": node_id or "unknown", "description": ""}
    if node_id:
        r = cg.search("ckg", {"tag": node_id.lower()})
        if r["hits"]["total"] > 0:
            activity["description"] = r["hits"]["hits"][0]["_source"].get(
                "statement", ""
            )
    return activity


def collect_project_info(chain_dir: str) -> dict:
    paths = ProjectService.resolve_paths(chain_dir)
    cg = CognitiveGraphSearcher(paths.project_root)
    info = {"language": "", "architecture": "", "patterns": [], "quality_gates": []}
    r = cg.search("ckg", {"tag": "architecture"})
    if r["hits"]["total"] > 0:
        info["architecture"] = r["hits"]["hits"][0]["_source"].get("statement", "")
    r = cg.search("ckg", {"type": "pattern"})
    info["patterns"] = [
        h["_source"].get("statement", "") for h in r["hits"]["hits"][:5]
    ]
    r = cg.search("ckg", {"type": "quality"})
    info["quality_gates"] = [
        h["_source"].get("statement", "") for h in r["hits"]["hits"][:5]
    ]
    return info


def collect_available_tasks(chain_dir: str, node_id: str | None = None) -> list[dict]:
    from features.engine.bootstrap import bootstrap

    registry, _ = bootstrap()
    tasks = registry.list_tasks_with_meta()
    result = []
    for t in tasks:
        result.append(
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "usage": t.get("usage", f"walker <project>/{t['name']}"),
            }
        )
    return result


def collect_knowledge(chain_dir: str, context: dict | None = None) -> dict:
    paths = ProjectService.resolve_paths(chain_dir)
    cg = CognitiveGraphSearcher(paths.project_root)
    ctx = context or {}
    global_knowledge = cg.search("ckg", {"tag": "global"})
    task_type = ctx.get("task_type", ctx.get("node_id", ""))
    relevant = (
        cg.search("ckg", {"tag": task_type}) if task_type else {"hits": {"hits": []}}
    )
    architecture = cg.search("ckg", {"tag": "architecture"})
    return {
        "global": [h["_source"] for h in global_knowledge["hits"]["hits"]],
        "relevant": [h["_source"] for h in relevant["hits"]["hits"]],
        "architecture": [h["_source"] for h in architecture["hits"]["hits"]],
    }


def collect_state(chain_dir: str) -> dict:
    # Node/history: workflow (motor). Sprint status: delta (fuente unica).
    node, sub_state, history = "", "", []
    try:
        from features.engine.infrastructure.repositories import WorkflowStateRepository

        wf = WorkflowStateRepository().load(chain_dir)
        node = wf.current_node
        sub_state = wf.current_sub_state
        history = wf.history[-5:] or []
    except Exception:
        pass
    sprint_id, status, task_id = "", "", ""
    try:
        from features.delta_tracking.application.services.spring_service import (
            SpringService,
        )

        spring_svc = SpringService.from_chain_dir(chain_dir)
        if spring_svc:
            spring_id = spring_svc.get_spring_id(chain_dir)
            spring_svc.ensure_spring_imported(chain_dir, spring_id)
            spring = spring_svc.get_spring(spring_id)
            if spring:
                sprint_id = spring.get("_id", spring_id)
                status = spring.get("status", "")
            cur = spring_svc.get_current_spec(spring_id)
            if cur:
                task_id = cur.get("spec_id", "")
    except Exception:
        pass
    return {
        "node": node,
        "sub_state": sub_state,
        "task_id": task_id,
        "sprint_id": sprint_id,
        "status": status,
        "history": history,
    }


def collect_all(
    chain_dir: str, node_id: str | None = None, context: dict | None = None
) -> dict:
    ctx = context or {}
    state = collect_state(chain_dir)
    activity = collect_activity(chain_dir, node_id)
    data = {
        "activity": activity,
        "project": collect_project_info(chain_dir),
        "tasks": collect_available_tasks(chain_dir, node_id),
        "knowledge": collect_knowledge(chain_dir, context),
        "state": state,
        "task_id": state.get("task_id", ctx.get("task_id", "")),
        "project_id": ctx.get("project_id", state.get("sprint_id", "")),
        "sprint_id": state.get("sprint_id", ""),
        "sprint_goal": ctx.get("sprint_goal", ""),
        "node": state.get("node", ""),
        "sub_state": state.get("sub_state", ""),
        "status": state.get("status", ""),
        "history": state.get("history", []),
        "session_id": ctx.get("session_id", ""),
        "chain_dir": chain_dir,
    }
    return data
