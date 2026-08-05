"""
application/task_synthesis.py — Síntesis de tareas desde predicates.

Portado desde ``to_clean_next/_internal/deep_analysis.py`` (protocolo de
6 capas). Se conserva SOLO el sub-pipeline de valor: transformar predicates
(must_exist / must_not_exist / invariants) en tareas tipadas del spring,
relacionadas con archivos reales del proyecto y con riesgo calculado.

PRINCIPIO: este módulo NO lee ni escribe plan.json/progress.json (ADR-WALKER-013).
Genera specs listas para ``SpringService.upsert_specs_batch`` / la propuesta
de planning. El protocolo de capas interactivo (init/submit_layer/finalize)
es legacy del skill delta y NO se porta.
"""

from __future__ import annotations

import re
from typing import Any, Callable


# ── Helpers ────────────────────────────────────────────────────


def task_base_fields() -> dict:
    """Campos base de una tarea (cumple schema vigente — formato delta)."""
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
    """Normaliza un chain_id para cumplir el schema ``^[a-z0-9-]+$``.

    El chain_id se deriva del basename del chain_dir; en macOS los
    directorios temporales pueden contener ``_`` (ej: ``tmpzno1o_4s``).
    """
    sanitized = re.sub(r"[^a-z0-9-]", "-", raw.lower())
    return sanitized.strip("-") or "chain"


# ── Archivos conocidos ─────────────────────────────────────────


def collect_known_files(known_files: list[str], tasks: list[dict]) -> list[str]:
    """Recolecta archivos conocidos desde tasks/backlog y fuentes previas.

    Args:
        known_files: lista inicial de archivos conocidos (repo, backlog).
            Acepta strings o listas anidadas (se aplanan).
        tasks: tareas con ``impacted_artifacts.files``.

    Returns:
        Lista deduplicada de archivos conocidos.
    """
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
    """Matchea un predicate contra los archivos conocidos (heuristica)."""
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


# ── Generación de tareas ───────────────────────────────────────


def generate_tasks_from_predicates(
    predicates: dict,
    project_id: str,
    known_files: list[str] | None = None,
    domains: list[str] | None = None,
) -> list[dict]:
    """Genera tareas (feature/bugfix/test) desde predicates normalizados.

    Args:
        predicates: dict con ``must_exist`` / ``must_not_exist`` / ``invariants``.
        project_id: id del proyecto (dominio por defecto).
        known_files: archivos conocidos para matchear impacted_artifacts.
        domains: dominios del proyecto.

    Returns:
        Lista de tareas tipadas según el tipo de predicate.
    """
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
    """Agrega una tarea fallback cuando no hay predicates."""
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
    """Completa task_context de cada tarea (requerido por schema)."""
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


# ── Riesgo ─────────────────────────────────────────────────────


def compute_total_risk(predicates: dict, complexity: float = 0.0) -> float:
    """Calcula el riesgo arquitectónico compuesto desde predicates.

    Args:
        predicates: dict con must_exist / must_not_exist / invariants.
        complexity: complejidad estructural adicional (0.0-1.0).

    Returns:
        Curvatura compuesta en [0.0, 0.5].
    """
    must_exist = predicates.get("must_exist", [])
    must_not_exist = predicates.get("must_not_exist", [])
    base_curvature = min(0.05 + (len(must_exist) + len(must_not_exist)) * 0.02, 0.40)
    struct_complexity = min(complexity * 0.10, 0.10)
    return min(base_curvature + struct_complexity, 0.50)


# ── Enriquecimiento de tasks existentes ────────────────────────


def enrich_tasks_with_predicates(
    tasks: list[dict],
    predicates: dict,
    known_files: list[str] | None = None,
) -> list[dict]:
    """Enriquece tasks existentes con type/files/riesgo derivados de predicates.

    Si una task no tiene ``validation.predicates``, se le asigna el primer
    predicate pendiente. Solo se sobreescribe ``impacted_artifacts.files``
    cuando la task no tiene archivos.

    Args:
        tasks: tareas de la propuesta (desde backlog o usuario).
        predicates: dict normalizado con must_exist/must_not_exist/invariants.
        known_files: archivos conocidos para matchear.

    Returns:
        Las mismas tareas enriquecidas (in-place + retorno).
    """
    known_files = known_files or []
    pool = list(predicates.get("must_exist", []))
    pool += list(predicates.get("must_not_exist", []))
    pool += list(predicates.get("invariants", []))
    pool_idx = 0

    for t in tasks:
        # Asignar predicate si falta
        if not t.get("validation", {}).get("predicates") and pool:
            pred = pool[pool_idx % len(pool)]
            pool_idx += 1
            t.setdefault("validation", {})["predicates"] = [pred]

        # Derivar type/work_type del primer predicate
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

        # Asignar files si no tiene
        arts = t.get("impacted_artifacts", {})
        if isinstance(arts, dict) and not arts.get("files"):
            t["impacted_artifacts"]["files"] = match_files_for(
                preds[0] if preds else "", known_files
            )

    return tasks


# ── API pública ────────────────────────────────────────────────

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
