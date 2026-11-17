"""scripts/skills_gate_audit.py — serie histórica del gate de skills.

El reporte (`skills_gate_report.py`) dice qué HARÍA el gate con el entorno
actual; esto dice qué HIZO: lee el trace del proyecto y agrupa los eventos
`skill_gate` (blocked | passed | no_candidates) y `skill_loaded` por nodo y task.

Uso:
    python3 scripts/skills_gate_audit.py [--project PATH] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

WALKER_ROOT = Path(__file__).resolve().parent.parent
if str(WALKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WALKER_ROOT))


def audit(project_root: str) -> dict:
    from fast_mcp.features.event_tracing.application.services.trace_factory import (
        build_trace_service,
    )
    from fast_mcp.shared_kernel.project_support import ProjectService

    paths = ProjectService.resolve_paths(project_root)
    service = build_trace_service(project_root)
    if service is None:
        return {"error": "no se pudo construir el TraceService", "project_root": project_root}

    events = service._repo.load_traces(project_root)  # noqa: SLF001 — script
    gates = [e for e in events if getattr(e, "event_type", "") == "skill_gate"]
    loaded = [e for e in events if getattr(e, "event_type", "") == "skill_loaded"]

    by_outcome: Counter = Counter()
    by_node: dict[str, Counter] = defaultdict(Counter)
    by_task: Counter = Counter()
    for event in gates:
        data = getattr(event, "data", None) or {}
        outcome = str(data.get("outcome", "?"))
        node = f"{data.get('node', '?')}.{data.get('sub_state', '?')}"
        by_outcome[outcome] += 1
        by_node[node][outcome] += 1
        by_task[str(getattr(event, "task_id", "") or "-")] += 1

    loaded_skills = Counter()
    for event in loaded:
        data = getattr(event, "data", None) or {}
        loaded_skills[str(data.get("skill", "?"))] += 1

    return {
        "project_root": project_root,
        "data_dir": str(paths.data_dir),
        "gates_total": len(gates),
        "by_outcome": dict(by_outcome),
        "by_node": {node: dict(counts) for node, counts in by_node.items()},
        "by_task": dict(by_task),
        "loaded_total": len(loaded),
        "loaded_skills": dict(loaded_skills),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(WALKER_ROOT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = audit(str(Path(args.project).resolve()))
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    if report.get("error"):
        print(f"error: {report['error']}", file=sys.stderr)
        return 2

    print(f"proyecto: {report['project_root']}")
    if not report["gates_total"]:
        print("sin eventos skill_gate todavía (el gate no se ejecutó o no hay trace)")
        return 0
    print(f"evaluaciones del gate: {report['gates_total']} → {report['by_outcome']}")
    print("\npor nodo:")
    for node, counts in sorted(report["by_node"].items()):
        print(f"  {node:<34} {counts}")
    print(f"\npor task: {report['by_task']}")
    print(f"skills cargados: {report['loaded_total']} → {report['loaded_skills']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
