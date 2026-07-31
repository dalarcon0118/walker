#!/usr/bin/env python3
"""
delta-transform.py — Transformaciones de datos entre nodos del workflow.

Cada subcomando transforma datos del nodo origen al nodo destino.
Son llamados por walker.py via hooks on_exit o transform en edges.

Usage:
    python3 delta-transform.py analysis-to-context <chain_dir> <task_id>
    python3 delta-transform.py advance-to-verify <chain_dir> <task_id>
"""

from __future__ import annotations
import json
import os
import sys


def load_json(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def analysis_to_context(chain_dir: str, task_id: str) -> int:
    """Transform: ANALYSIS_GATE → ADVANCE_TASK

    Lee contract_data.ADVANCE_TASK[task_id] y lo escribe en
    current.analysis_3layer[task_id] para compatibilidad con check-analysis.
    """
    prog_path = os.path.join(chain_dir, "progress.json")
    raw = load_json(prog_path)
    if not raw:
        return 2
    prog = raw.get("progress", raw)
    cd = (
        prog.setdefault("current", {})
        .setdefault("contract_data", {})
        .get("ADVANCE_TASK", {})
        .get(task_id)
    )
    if not cd:
        eprint("No hay datos de analisis en contract_data")
        return 1

    # Copy to analysis_3layer for compatibility
    prog["current"].setdefault("analysis_3layer", {})[task_id] = cd
    raw.pop("_checksum", None)
    try:
        with open(prog_path, "w") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)
    except OSError:
        pass
    print(f"analysis_to_context: {task_id} transferido")
    return 0


def advance_to_verify(chain_dir: str, task_id: str) -> int:
    """Transform: ADVANCE_TASK → VERIFY

    Verifica que la tarea tenga predicates antes de pasar a QA.
    """
    plan_path = os.path.join(chain_dir, "plan.json")
    plan = load_json(plan_path)
    if not plan:
        return 2
    pl = plan.get("plan", plan)
    for t in pl.get("tasks", []):
        if t.get("task_id") == task_id:
            preds = t.get("validation", {}).get("predicates", [])
            if not preds:
                eprint(f"Tarea {task_id} no tiene predicates definidos")
                return 1
            print(f"advance_to_verify: {task_id} con {len(preds)} predicates")
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
        "analysis-to-context": lambda: analysis_to_context(chain_dir, task_id or ""),
        "advance-to-verify": lambda: advance_to_verify(chain_dir, task_id or ""),
    }

    handler = dispatch.get(sub)
    if not handler:
        eprint(f"Transform desconocido: {sub}")
        return 2
    return handler()


if __name__ == "__main__":
    sys.exit(main())
