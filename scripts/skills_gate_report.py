"""scripts/skills_gate_report.py — qué haría el gate de skills en ESTE entorno.

Responde la pregunta que no se puede contestar leyendo logs: de todos los
sub-estados que declaran `skill_intent`, ¿en cuántos el gate bloquearía y en
cuántos pasaría por "sin candidatos"? Es la foto de hoy; la serie histórica sale
del trace (`skill_gate`), que el orquestador emite en cada submit.

Uso:
    python3 scripts/skills_gate_report.py [--project PATH] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

WALKER_ROOT = Path(__file__).resolve().parent.parent
if str(WALKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WALKER_ROOT))

from fast_mcp.features.workflow.application.orchestrator import (  # noqa: E402
    MIN_SKILL_SCORE_REQUIRED,
    OrchestrationService,
)
from fast_mcp.features.workflow.infrastructure.graph_repository import (  # noqa: E402
    GraphRepository,
)
from fast_mcp.shared_kernel.skills import (  # noqa: E402
    discover,
    score,
    skills_roots,
)


def _orchestrator() -> OrchestrationService:
    # Instancia sin __init__: solo se usa _skill_candidates, que depende de
    # ctx.chain_dir, no del bundle.
    return object.__new__(OrchestrationService)


def collect(project_root: Path) -> dict:
    graph = GraphRepository(str(WALKER_ROOT / "data" / "workflow" / "workflow.json")).load()
    skills_all = discover(skills_roots(project_root, WALKER_ROOT))
    orch = _orchestrator()
    ctx = SimpleNamespace(chain_dir=str(project_root), task_id="", session_id="")

    rows: list[dict] = []
    for node_id, node in graph.nodes.items():
        for sub in node.sub_states:
            intent = getattr(sub, "skill_intent", None) or {}
            if not intent:
                continue
            candidates = orch._skill_candidates(ctx, intent)  # noqa: SLF001
            needs = str(intent.get("needs") or "")
            tags = intent.get("tags") or ()
            scored = [
                (name, score(skills_all[name], needs, tags))
                for name in candidates if name in skills_all
            ]
            policy = str(intent.get("policy") or "advisory")
            strong = [name for name, sc in scored if sc >= MIN_SKILL_SCORE_REQUIRED]
            if policy != "required":
                verdict = "advisory"
            elif strong:
                verdict = "bloquearía_sin_carga"
            else:
                verdict = "pasa_sin_candidatos"
            rows.append({
                "node": node_id,
                "sub_state": sub.key,
                "execution": getattr(getattr(sub, "execution", None), "value", ""),
                "policy": policy,
                "candidates": candidates,
                "scores": scored,
                "verdict": verdict,
            })

    skills = discover(skills_roots(project_root, WALKER_ROOT))
    return {"rows": rows, "skills": sorted(skills), "project_root": str(project_root)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(WALKER_ROOT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = collect(Path(args.project).resolve())
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    print(f"proyecto: {report['project_root']}")
    print(f"skills en el entorno: {len(report['skills'])} → {', '.join(report['skills']) or '(ninguno)'}")
    print()
    if not report["rows"]:
        print("ningún sub-estado declara skill_intent")
        return 0

    print(f"{'nodo/sub-estado':<40} {'policy':<9} {'veredicto':<24} candidatos")
    for row in report["rows"]:
        key = f"{row['node']}.{row['sub_state']}"[:40]
        cands = ", ".join(f"{n}({sc})" for n, sc in row["scores"][:3]) or "-"
        print(f"{key:<40} {row['policy']:<9} {row['verdict']:<24} {cands}")

    counts: dict[str, int] = {}
    for row in report["rows"]:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    print("\nresumen:", counts)
    print(f"umbral de candidato para gate required: score >= {MIN_SKILL_SCORE_REQUIRED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
