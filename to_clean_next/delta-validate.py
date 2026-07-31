#!/usr/bin/env python3
"""
delta-validate.py — Validaciones de schema y datos para el workflow.

Centraliza la logica de validacion que antes estaba dispersa en spring.py
y walker.py. Los subcomandos son llamados desde gate_validation en
workflow.json o desde hooks on_exit/transform.

Usage:
    python3 delta-validate.py ensure-v3 <chain_dir>
    python3 delta-validate.py check-task <chain_dir> <task_id>
"""

from __future__ import annotations
import json
import os
import sys

_V3_DEFAULTS = {
    "task_description": "",
    "importer_files": 0,
    "depends_on": [],
    "preconditions": [],
    "acceptance_criteria": [],
    "quality_gates": [],
    "loop_verification": [],
    "loop_acceptance": "all",
    "decisions": [],
    "refinement": {},
    "risks": {"curvature": 0.2, "pre_churn": 0.1, "commit_trust": 0.9},
    "impacted_artifacts": {"files": []},
    "validation": {"predicates": []},
    "task_context": {
        "observation": "Contexto generado.",
        "structure": "Contexto generado.",
        "behavior": "Contexto generado.",
        "causality": "Contexto generado.",
        "purpose": "Contexto generado.",
        "principles": "Contexto generado.",
    },
}


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def load_json(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def ensure_v3(chain_dir: str) -> int:
    """Apply v3 defaults to all tasks in plan.json.

    Corrige tipos incorrectos (ej: [] en vez de 0) y agrega campos faltantes.
    """
    plan_path = os.path.join(chain_dir, "plan.json")
    plan = load_json(plan_path)
    if not plan:
        eprint("plan.json no encontrado")
        return 2

    pl = plan.get("plan", plan)
    fixed = 0
    for task in pl.get("tasks", []):
        for k, v in _V3_DEFAULTS.items():
            expected_type = type(v)
            if k in task:
                if type(task[k]) != expected_type:
                    task[k] = v
                    fixed += 1
            else:
                task[k] = v
                fixed += 1

    if fixed > 0:
        with open(plan_path, "w") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)
        print(
            f"ensure-v3: {fixed} campos corregidos en {len(pl.get('tasks', []))} tareas"
        )
    return 0


def check_task(chain_dir: str, task_id: str) -> int:
    """Validate a single task has all required v3 fields."""
    plan_path = os.path.join(chain_dir, "plan.json")
    plan = load_json(plan_path)
    if not plan:
        return 2
    pl = plan.get("plan", plan)
    for t in pl.get("tasks", []):
        if t.get("task_id") == task_id:
            missing = [k for k, v in _V3_DEFAULTS.items() if k not in t]
            if missing:
                eprint(f"Tarea {task_id} sin campos: {', '.join(missing)}")
                return 1
            wrong_type = [
                k for k, v in _V3_DEFAULTS.items() if k in t and type(t[k]) != type(v)
            ]
            if wrong_type:
                eprint(f"Tarea {task_id} con tipo incorrecto: {', '.join(wrong_type)}")
                return 1
            return 0
    eprint(f"Tarea {task_id} no encontrada")
    return 1


def main() -> int:
    if len(sys.argv) < 3:
        eprint(__doc__)
        return 2
    sub = sys.argv[1]
    chain_dir = os.path.abspath(sys.argv[2])
    task_id = sys.argv[3] if len(sys.argv) > 3 else None

    dispatch = {
        "ensure-v3": lambda: ensure_v3(chain_dir),
        "check-task": lambda: check_task(chain_dir, task_id or ""),
    }

    handler = dispatch.get(sub)
    if not handler:
        eprint(f"Subcomando invalido: {sub}")
        return 2
    return handler()


if __name__ == "__main__":
    sys.exit(main())
