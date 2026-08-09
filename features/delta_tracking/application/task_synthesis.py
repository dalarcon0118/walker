"""application/task_synthesis.py — Síntesis de tareas desde predicates.

Movido desde features/spring/application/task_synthesis.py.
delta_tracking es el SSOT/SRP de todo lo relacionado con el spring.
"""
from __future__ import annotations

import re
from typing import Any


def task_base_fields() -> dict:
    return {
        "role": "core",
        "scrum_role": "dev",
        "importer_files": 0,
        "depends_on": [],
        "preconditions": [],
        "quality_gates": [],
        "loop_verification": [],
        "loop_acceptance": "all",
        "decisions": [],
        "refinement": {"attempts": 0, "dead_ends": []},
    }


def sanitize_chain_id(raw: str) -> str:
    sanitized = re.sub(r"[^a-z0-9-]", "-", raw.lower())
    return sanitized.strip("-") or "chain"


def collect_known_files(known_files: list[str], tasks: list[dict]) -> list[str]:
    known: list[str] = []

    def _add(f: Any) -> None:
        if isinstance(f, str) and f and f not in known:
            known.append(f)

    for item in known_files:
        if isinstance(item, list):
            for f in item:
                _add(f)
        else:
            _add(item)
    for t in tasks:
        arts = t.get("impacted_artifacts", {})
        if isinstance(arts, dict):
            files = arts.get("files", [])
        elif isinstance(arts, list):
            files = arts
        else:
            files = []
        for f in files:
            _add(f)
    return known


def match_files_for(predicate: str, known_files: list[str]) -> list[str]:
    matched: list[str] = []
    pred_lower = predicate.lower()
    for f in known_files:
        parts = (
            f.replace("/", " ")
            .replace("\\", " ")
            .replace("_", " ")
            .replace("-", " ")
            .lower()
            .split()
        )
        if any(p in pred_lower for p in parts if len(p) > 2):
            matched.append(f)
        if len(matched) >= 3:
            break
    if not matched and known_files:
        matched = [known_files[0]]
    elif not matched:
        matched = ["src/main.py"]
    return matched


def generate_tasks_from_predicates(
    predicates: dict,
    project_id: str,
    known_files: list[str] | None = None,
    domains: list[str] | None = None,
) -> list[dict]:
    must_exist = predicates.get("must_exist", [])
    must_not_exist = predicates.get("must_not_exist", [])
    invariants = predicates.get("invariants", [])

    known_files = known_files or []
    domains = domains or [project_id]

    tasks: list[dict] = []
    task_num = 1

    for p in must_exist:
        p_domain = _pick_domain(p, domains, project_id)
        p_type = "feature"
        p_work_type = "refactor"
        if "fix:" in p:
            p_type = "bugfix"
            p_work_type = "bugfix"

        tasks.append(
            {
                **task_base_fields(),
                "task_id": f"TASK-{task_num:03d}",
                "type": p_type,
                "work_type": p_work_type,
                "objective": f"Implementar {p}",
                "result_state": f"{p} implementado y verificado",
                "task_description": f"Implementar: {p}",
                "domain": p_domain,
                "validation": {"predicates": [p]},
                "impacted_artifacts": {"files": match_files_for(p, known_files)},
                "risks": {"curvature": 0.15},
                "acceptance_criteria": [f"{p} implementado y verificado"],
            }
        )
        task_num += 1

    for p in must_not_exist:
        p_domain = _pick_domain(p, domains, project_id)
        tasks.append(
            {
                **task_base_fields(),
                "task_id": f"TASK-{task_num:03d}",
                "type": "bugfix",
                "work_type": "bugfix",
                "objective": f"Eliminar o migrar {p}",
                "result_state": f"{p} eliminado",
                "task_description": f"Eliminar o migrar: {p}",
                "domain": p_domain,
                "validation": {"predicates": [f"NO {p}"]},
                "impacted_artifacts": {"files": match_files_for(p, known_files)},
                "risks": {"curvature": 0.20},
                "acceptance_criteria": [f"{p} eliminado"],
            }
        )
        task_num += 1

    for inv in invariants:
        p_domain = _pick_domain(inv, domains, project_id)
        tasks.append(
            {
                **task_base_fields(),
                "task_id": f"TASK-{task_num:03d}",
                "type": "test",
                "work_type": "refactor",
                "objective": f"Verificar: {inv}",
                "result_state": f"{inv} verificado",
                "task_description": f"Verificar: {inv}",
                "domain": p_domain,
                "validation": {"predicates": [f"invariant: {inv}"]},
                "impacted_artifacts": {"files": match_files_for(inv, known_files)},
                "risks": {"curvature": 0.10},
                "acceptance_criteria": [f"{inv} verificado"],
            }
        )
        task_num += 1

    if not tasks:
        tasks.append(_fallback_task(project_id, "Sprint goal", known_files))

    return tasks


def _pick_domain(predicate: str, domains: list[str], project_id: str) -> str:
    for d in domains:
        if d.lower() in predicate.lower():
            return d
    return domains[0] if domains else project_id


def _fallback_task(project_id: str, goal: str, known_files: list[str]) -> dict:
    fallback_files = known_files[:1] if known_files else ["src/main.py"]
    return {
        **task_base_fields(),
        "task_id": "TASK-001",
        "type": "feature",
        "work_type": "refactor",
        "objective": goal[:200],
        "result_state": f"{goal[:100]} completado",
        "task_description": goal[:100],
        "domain": project_id,
        "validation": {"predicates": [goal[:200]]},
        "impacted_artifacts": {"files": fallback_files},
        "risks": {"curvature": 0.20},
        "acceptance_criteria": [f"{goal[:100]} completado"],
    }


def fill_task_contexts(tasks: list[dict], goal: str, project_id: str) -> None:
    for t in tasks:
        obj = t.get("objective", goal)[:100]
        files = t.get("impacted_artifacts", {}).get("files", [])
        file_list = ", ".join(files) if files else "por determinar"
        domain = t.get("domain", project_id)
        t["task_context"] = {
            "observation": f"Objetivo: {obj}. Archivos: {file_list}.",
            "structure": f"Archivos {file_list} en dominio {domain}.",
            "behavior": f"Implementar: {obj}.",
            "causality": "Restricciones del analisis.",
            "purpose": f"Cumplir goal: {goal[:80]}.",
            "principles": f"Convenciones del proyecto en {domain}.",
        }
        if not t.get("task_description"):
            t["task_description"] = obj
        if not t.get("acceptance_criteria"):
            t["acceptance_criteria"] = [t.get("result_state", obj)]


def compute_total_risk(predicates: dict, complexity: float = 0.0) -> float:
    must_exist = predicates.get("must_exist", [])
    must_not_exist = predicates.get("must_not_exist", [])
    base_curvature = min(0.05 + (len(must_exist) + len(must_not_exist)) * 0.02, 0.40)
    struct_complexity = min(complexity * 0.10, 0.10)
    return min(base_curvature + struct_complexity, 0.50)


def enrich_tasks_with_predicates(
    tasks: list[dict],
    predicates: dict,
    known_files: list[str] | None = None,
) -> list[dict]:
    known_files = known_files or []
    pool = list(predicates.get("must_exist", []))
    pool += list(predicates.get("must_not_exist", []))
    pool += list(predicates.get("invariants", []))
    pool_idx = 0

    for t in tasks:
        if not t.get("validation", {}).get("predicates") and pool:
            pred = pool[pool_idx % len(pool)]
            pool_idx += 1
            t.setdefault("validation", {})["predicates"] = [pred]

        preds = t.get("validation", {}).get("predicates", [])
        if preds:
            pred0 = preds[0]
            if pred0.startswith("NO ") or "fix:" in pred0:
                t["type"] = "bugfix"
                t["work_type"] = "bugfix"
            elif pred0.startswith("invariant:"):
                t["type"] = "test"
                t["work_type"] = "refactor"
            else:
                t["type"] = "feature"
                t["work_type"] = "refactor"

        arts = t.get("impacted_artifacts", {})
        if isinstance(arts, dict) and not arts.get("files"):
            t["impacted_artifacts"]["files"] = match_files_for(
                preds[0] if preds else "", known_files
            )

    return tasks


__all__ = [
    "task_base_fields",
    "sanitize_chain_id",
    "collect_known_files",
    "match_files_for",
    "generate_tasks_from_predicates",
    "fill_task_contexts",
    "compute_total_risk",
    "enrich_tasks_with_predicates",
]
