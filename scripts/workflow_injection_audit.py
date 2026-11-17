"""scripts/workflow_injection_audit.py — ¿el agente sigue el workflow?

El plugin inyecta el estado del workflow en cada llamada al modelo y traza el
resultado al cerrar la sesión (`workflow_state_injected` con `injections` y
`followed`, más `workflow_suggestion_ignored` si no hubo submit/transition).

Este script agrega esos eventos y calcula la tasa de cumplimiento. Es la
evidencia que decide si vale la pena un gate duro (bloquear) o si el agente ya
sigue la sugerencia y bloquear solo agrega fricción.

Uso:
    python3 scripts/workflow_injection_audit.py [--project PATH] [--json]
    python3 scripts/workflow_injection_audit.py --min-sample 20
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

WALKER_ROOT = Path(__file__).resolve().parent.parent
if str(WALKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WALKER_ROOT))


def audit(project_root: str) -> dict:
    from fast_mcp.features.event_tracing.application.services.trace_factory import (
        build_trace_service,
    )

    service = build_trace_service(project_root)
    if service is None:
        return {"error": "no se pudo construir el TraceService"}
    events = service._repo.load_traces(project_root)  # noqa: SLF001 — script

    by_session: dict[str, dict] = defaultdict(lambda: {
        "injections": 0, "followed": False, "ignored": False, "position": "",
    })
    for event in events:
        etype = getattr(event, "event_type", "")
        if etype not in ("workflow_state_injected", "workflow_suggestion_ignored"):
            continue
        meta = getattr(event, "metadata", None) or {}
        sid = str(meta.get("session_id") or getattr(event, "session_id", "") or "-")
        entry = by_session[sid]
        if etype == "workflow_state_injected":
            entry["injections"] = max(entry["injections"], int(meta.get("injections") or 1))
            entry["followed"] = entry["followed"] or bool(meta.get("followed"))
            entry["position"] = entry["position"] or str(meta.get("position") or "")
        else:
            entry["ignored"] = True
            entry["position"] = entry["position"] or str(meta.get("position") or "")

    sessions = [{"session": sid, **data} for sid, data in by_session.items()]
    sessions.sort(key=lambda row: (row["ignored"], row["session"]))
    total = len(sessions)
    ignored = sum(1 for row in sessions if row["ignored"])
    followed = sum(1 for row in sessions if row["followed"] and not row["ignored"])
    return {
        "sessions": total,
        "injections": sum(row["injections"] for row in sessions),
        "followed": followed,
        "ignored": ignored,
        "ignored_rate": round(ignored / total, 3) if total else 0.0,
        "detail": sessions,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(WALKER_ROOT))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--min-sample", type=int, default=20,
                    help="sesiones mínimas para decidir un gate duro")
    args = ap.parse_args()

    report = audit(str(Path(args.project).resolve()))
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    if report.get("error"):
        print(f"error: {report['error']}", file=sys.stderr)
        return 2

    print(f"sesiones con inyección: {report['sessions']} · inyecciones: {report['injections']}")
    print(f"seguidas: {report['followed']} · ignoradas: {report['ignored']} "
          f"(tasa {report['ignored_rate']:.0%})")
    for row in report["detail"][:10]:
        state = "seguida" if row["followed"] and not row["ignored"] else (
            "ignorada" if row["ignored"] else "sin cierre")
        print(f"  {row['session'][:28]:<28} {row['injections']:>2} iny · {state:<9} {row['position']}")
    if report["sessions"] < args.min_sample:
        print(f"\nmuestra insuficiente ({report['sessions']}/{args.min_sample}): "
              "no hay base para decidir un gate duro todavía")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
