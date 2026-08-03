#!/usr/bin/env python3
"""
domain/enforce.py — Capa de COERCIÓN del skill delta.

Migrado desde ``to_clean_next/_internal/enforce.py`` (delta-enforce.py).
Es la pieza que convierte el protocolo del skill (advisory) en enforcement
duro: bloquea (ok=False) si la tarea:

  1. next_action      → no es la next_action de progress.json (avance fuera de orden)
  2. curvature_budget → excede el presupuesto de curvature_risk (impacto arquitectónico)
  3. distance_guard   → no tiene predicates de validación (no reduce la distancia al G_objetivo)
  4. backlog_required → no está en plan.json y tampoco en backlog.jsonl (tarea fuera de scope)

Diferencias vs legacy:
  - Servicio de dominio PURO: ``enforce_task`` recibe ``plan`` y ``progress``
    ya cargados (dicts). El IO (lectura de archivos) vive en el caller.
  - El presupuesto de curvature se inyecta como parámetro (el caller lo
    resuelve de config/env) en lugar de leer os.environ dentro del dominio.
  - La verificación de checksum de progress.json se delega al caller
    (que decide si es STRICT y hace exit) — aquí solo se expone el helper.
  - Se corrige la lectura del ``next_action``: en el legacy se leía a nivel
    raíz/progress del dict YA desenvuelto, lo que perdía variantes v3.0
    (next_action bajo ``progress.current``) — aquí se maneja explícitamente.

Uso (caller):
    plan = load_json(os.path.join(chain_dir, "plan.json"))
    progress = load_json(os.path.join(chain_dir, "progress.json"))
    ok, reasons, warnings = enforce_task(plan, progress, task_id, force=force)
"""

from __future__ import annotations

import json
from typing import Any

CURVATURE_BUDGET_DEFAULT = 0.50


# ── Helpers puros ──────────────────────────────────────────────


def get_next_action(progress: dict) -> str | None:
    """Extrae el task_id de la next_action desde los esquemas v2.0 / v3.0.

    - v2.0: progress.current.next_action
    - v3.0: progress.next_action o progress.progress.next_action
    """
    prog = progress.get("progress", progress)
    na = (prog.get("current") or {}).get("next_action")
    if isinstance(na, dict):
        return na.get("task_id")
    na = progress.get("next_action")
    if isinstance(na, dict):
        return na.get("task_id")
    na = prog.get("next_action")
    if isinstance(na, dict):
        return na.get("task_id")
    return None


def get_project_id(plan: dict) -> str | None:
    """Extract project_id from plan data (multi-project support)."""
    try:
        pl = plan.get("plan", plan)
        return pl.get("project", {}).get("project_id") or None
    except Exception:
        return None


def find_backlog_path(project_id: str | None, project_root: str) -> str:
    """Ruta del backlog.jsonl (multi-project si hay project_id)."""
    if project_id:
        return (
            f"{project_root}/.cognitive/projects/{project_id}/delta/backlog.jsonl"
        )
    return f"{project_root}/.cognitive/delta/backlog.jsonl"


def _task_in_backlog(backlog_path: str, task_id: str) -> bool:
    """Busca task_id en backlog.jsonl (formato JSONL, una tarea por línea)."""
    try:
        with open(backlog_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    bj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if bj.get("id") == task_id or task_id in (bj.get("description") or ""):
                    return True
    except (OSError, FileNotFoundError):
        return False
    return False


# ── Enforcement puro ───────────────────────────────────────────


def enforce_task(
    plan: dict | None,
    progress: dict | None,
    task_id: str,
    force: bool = False,
    curvature_budget: float = CURVATURE_BUDGET_DEFAULT,
    project_root: str = "",
) -> tuple[bool, list[dict], list[str]]:
    """Evalúa una tarea contra el goal del sprint.

    Returns (ok, reasons, warnings).
    ok=False → bloquear salvo ``force``.
    """
    reasons: list[dict] = []
    warnings: list[str] = []

    if plan is None or progress is None:
        # Sin plan/progress no podemos evaluar → permitir (no es responsabilidad de este gate)
        return True, reasons, warnings

    pl = plan.get("plan", plan)
    prog = progress.get("progress", progress)

    # ── Regla 1: next_action ───────────────────────────────────────
    # Defense-in-depth: si la tarea ya está en ready_for_review, se salta
    # el check de next_action porque el TASK LOOP ya la validó.
    task_state = (prog.get("current") or {}).get("task_states", {}).get(task_id)
    if task_state != "ready_for_review":
        na = get_next_action(progress)
        if na is not None and na != "__ALL_DONE__" and na != task_id:
            reasons.append(
                {
                    "rule": "next_action",
                    "message": (
                        f"La tarea '{task_id}' NO es la next_action del sprint ('{na}'). "
                        "Avanzar fuera de orden es desviación del goal."
                    ),
                }
            )

    # Localizar la tarea en plan.json
    task = None
    for t in pl.get("tasks", []):
        if t.get("task_id") == task_id:
            task = t
            break

    # ── Regla 2: curvature_budget ──────────────────────────────────
    if task is not None:
        risks = task.get("risks", {}) or {}
        curvature = risks.get("curvature", risks.get("curvature_risk", 0.20))
        try:
            curvature = float(curvature)
        except (TypeError, ValueError):
            curvature = 0.20
        budget = float(curvature_budget)
        if curvature > budget:
            reasons.append(
                {
                    "rule": "curvature_budget",
                    "message": (
                        f"curvature_risk={curvature:.2f} > presupuesto={budget:.2f}. "
                        "La tarea tiene alto impacto arquitectónico y se aleja del camino planeado."
                    ),
                }
            )

        # ── Regla 3: distance_guard (advertencia, no bloquea) ──────
        preds = task.get("validation", {}).get("predicates", [])
        if not preds and not task.get("result_state"):
            warnings.append(
                f"La tarea '{task_id}' no tiene predicates de validación ni result_state: "
                "no reduce la distancia al G_objetivo (posible drift)."
            )
    else:
        # ── Regla 4: backlog_required (tarea fuera de plan.json) ───
        project_id = get_project_id(plan)
        backlog_path = find_backlog_path(project_id, project_root)
        found = _task_in_backlog(backlog_path, task_id)
        if not found:
            reasons.append(
                {
                    "rule": "backlog_required",
                    "message": (
                        f"La tarea '{task_id}' NO está en plan.json y NO está registrada en "
                        "backlog.jsonl. Toda tarea fuera de scope debe registrarse en el backlog "
                        "antes de avanzar (o usar --force)."
                    ),
                }
            )

    ok = (len(reasons) == 0) or force
    return ok, reasons, warnings


def enforce_task_json_result(
    plan: dict | None,
    progress: dict | None,
    task_id: str,
    force: bool = False,
    curvature_budget: float = CURVATURE_BUDGET_DEFAULT,
    project_root: str = "",
) -> dict:
    """Variante JSON de enforce_task (equivalente a --json del CLI legacy)."""
    ok, reasons, warnings = enforce_task(
        plan,
        progress,
        task_id,
        force=force,
        curvature_budget=curvature_budget,
        project_root=project_root,
    )
    return {
        "ok": ok,
        "reasons": reasons,
        "warnings": warnings,
        "overridden": bool(force and reasons),
    }


__all__ = [
    "enforce_task",
    "enforce_task_json_result",
    "get_next_action",
    "get_project_id",
    "find_backlog_path",
    "CURVATURE_BUDGET_DEFAULT",
]
