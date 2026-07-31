#!/usr/bin/env python3
"""
delta-gate-check.py — Gate validation helpers for the workflow engine.

Subcomandos para validar gates desde workflow.json sin depender de flags
inexistentes en spring.py. Cada subcomando imprime el resultado y retorna
exit code según la logica del gate.

Usage:
    python3 delta-gate-check.py pending-count <chain_dir>
    python3 delta-gate-check.py sprint-count <chain_dir>
    python3 delta-gate-check.py health-score <chain_dir>
    python3 delta-gate-check.py check-dependencies <chain_dir> <task_id>
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def load_json(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None




def cmd_pending_count(chain_dir: str) -> int:
    """Count pending tasks. Exit 1 if > 0 pendientes."""
    prog = load_json(os.path.join(chain_dir, "progress.json"))
    if not prog:
        eprint("No se pudo leer progress.json")
        return 2

    ts = prog.get("progress", prog).get("current", {}).get("task_states", {})
    done_statuses = {"completed", "achieved", "cancelled", "closed", "done"}
    pendings = [
        tid
        for tid, s in ts.items()
        if isinstance(s, str) and s.lower() not in done_statuses
    ]
    count = len(pendings)
    # Output "1" si hay pendientes, "0" si no (para output_to_gate del workflow)
    print("1" if count > 0 else "0")
    return 1 if count > 0 else 0


def cmd_sprint_count(chain_dir: str) -> int:
    """Count sprints in the project. Exit 0 if > 0 existen."""
    # Derive project_id from chain_dir path: .../projects/<pid>/delta/<chain>
    # We look one level above 'delta' to find the project directory
    chain_dir = os.path.abspath(chain_dir)
    parent = os.path.dirname(chain_dir)  # .../projects/<pid>/delta

    # Scan project's delta dir for sprints
    delta_dir = parent if os.path.basename(parent) == "delta" else None
    if delta_dir and os.path.isdir(delta_dir):
        sprints = [
            d
            for d in os.listdir(delta_dir)
            if os.path.isfile(os.path.join(delta_dir, d, "progress.json"))
        ]
    else:
        sprints = []

    count = len(sprints)
    print(count)
    return 0 if count > 0 else 1


def cmd_health_score(chain_dir: str) -> int:
    """Delegate to spring.py health and parse the score. Exit 0 si > 80."""
    spring_script = os.path.join(os.path.dirname(__file__), "spring.py")
    try:
        r = subprocess.run(
            [sys.executable, spring_script, "health", chain_dir],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        eprint(f"Error ejecutando spring.py health: {e}")
        return 2

    # Parse "Health Score: 57.5/100" from output
    m = re.search(r"Health Score:\s*([\d.]+)", r.stdout)
    if not m:
        eprint("No se pudo parsear health score del output")
        return 2

    score = float(m.group(1))
    # Output "1" si health ok (>80), "0" si fail (para output_to_gate del workflow)
    print("1" if score > 80 else "0")
    return 0 if score > 80 else 1


def cmd_predicates_pass(chain_dir: str) -> int:
    """Verify completed tasks have predicates. Exit 0 = ok."""
    prog = load_json(os.path.join(chain_dir, "progress.json"))
    plan = load_json(os.path.join(chain_dir, "plan.json"))
    if not prog or not plan:
        return 2
    ts = prog.get("progress", prog).get("current", {}).get("task_states", {})
    pl = plan.get("plan", plan)
    tasks = {t.get("task_id"): t for t in pl.get("tasks", [])}
    for tid, status in ts.items():
        if isinstance(status, str) and status.lower() in ("completed", "achieved"):
            preds = tasks.get(tid, {}).get("validation", {}).get("predicates", [])
            if not preds:
                eprint(f"Tarea {tid} no tiene predicates")
                return 1
    return 0


def cmd_check_dependencies(chain_dir: str, task_id: str) -> int:
    """Check if all dependencies of a task are resolved.

    Lee plan.json para obtener depends_on de la tarea.
    Verifica task_states en progress.json para cada dependencia.
    Exit 0 si todas resueltas, 1 si hay pendientes (lista en stderr).

    Usage:
        python3 delta-gate-check.py check-dependencies <chain_dir> <task_id>
    """
    plan = load_json(os.path.join(chain_dir, "plan.json"))
    prog = load_json(os.path.join(chain_dir, "progress.json"))
    if not plan or not prog:
        eprint("No se pudo leer plan.json o progress.json")
        return 2

    pl = plan.get("plan", plan)
    tasks = {t.get("task_id"): t for t in pl.get("tasks", [])}

    task = tasks.get(task_id)
    if not task:
        eprint(f"Tarea no encontrada: {task_id}")
        return 2

    depends_on = task.get("depends_on", [])
    if not depends_on:
        print("0")
        return 0

    ts = prog.get("progress", prog).get("current", {}).get("task_states", {})
    done_statuses = {"completed", "achieved", "cancelled", "closed", "done"}

    unresolved = []
    for dep_id in depends_on:
        status = ts.get(dep_id, "unknown")
        if isinstance(status, str) and status.lower() not in done_statuses:
            unresolved.append(dep_id)

    if unresolved:
        eprint(f"Dependencias no resueltas: {', '.join(unresolved)}")
        return 1

    print("0")
    return 0


_HANDLERS_WITH_TASK_ID = {"check-dependencies"}


def main() -> int:
    if len(sys.argv) < 3:
        eprint(__doc__)
        return 2

    sub = sys.argv[1]
    chain_dir = os.path.abspath(sys.argv[2])

    if not os.path.isdir(chain_dir):
        eprint(f"Directorio no encontrado: {chain_dir}")
        return 2

    dispatch = {
        "pending-count": cmd_pending_count,
        "sprint-count": cmd_sprint_count,
        "health-score": cmd_health_score,
        "predicates-pass": cmd_predicates_pass,
        "check-dependencies": cmd_check_dependencies,
    }

    handler = dispatch.get(sub)
    if not handler:
        eprint(f"Subcomando invalido: {sub}")
        eprint(f"  Validos: {', '.join(sorted(dispatch.keys()))}")
        return 2

    if sub in _HANDLERS_WITH_TASK_ID:
        task_id = sys.argv[3] if len(sys.argv) > 3 else ""
        return handler(chain_dir, task_id)

    return handler(chain_dir)


if __name__ == "__main__":
    sys.exit(main())
