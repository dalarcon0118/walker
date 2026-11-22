#!/usr/bin/env python3
"""Reporta turnos humanos por tarea: baseline (4 gates) vs post-fusión (2 gates).

Cuenta transiciones de gates humanos en el trace del proyecto:
  PLAN_APPROVAL, USER_GATE, REPORT_GATE, PO_ACCEPT.

Uso: python3 scripts/human_turns_report.py [--chain-dir PATH] [--session-id SID]
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HUMAN_GATES = {"PLAN_APPROVAL", "USER_GATE", "REPORT_GATE", "PO_ACCEPT"}
POST_FUSION_GATES = {"PLAN_APPROVAL", "REPORT_GATE"}  # USER_GATE y PO_ACCEPT absorbidos


def main() -> None:
    chain_dir = str(ROOT)
    session_id = ""
    args = sys.argv[1:]
    if "--chain-dir" in args:
        idx = args.index("--chain-dir")
        chain_dir = args[idx + 1]
    if "--session-id" in args:
        idx = args.index("--session-id")
        session_id = args[idx + 1]

    from fast_mcp.features.event_tracing.application.services.trace_factory import (
        build_trace_service,
    )

    service = build_trace_service(chain_dir)
    if service is None:
        print("ERROR: no se pudo construir el TraceService")
        sys.exit(1)
    events = service.load_traces(chain_dir)

    # Filtrar por sesión si se puede (metadata.session_id o data.session_id).
    if session_id:
        filtered = []
        for e in events:
            meta = getattr(e, "metadata", None) or {}
            data = getattr(e, "data", None) or {}
            sid = str(meta.get("session_id") or data.get("session_id") or "")
            if sid == session_id:
                filtered.append(e)
        events = filtered

    # Contar transiciones de gates humanos.
    gate_transitions: Counter = Counter()
    tasks: defaultdict[str, set] = defaultdict(set)
    for e in events:
        if e.event_type != "TRANSITION":
            continue
        data = getattr(e, "data", None) or {}
        frm = str(data.get("from", "") or "")
        to = str(data.get("to", "") or "")
        gate = str(data.get("gate", "") or "")
        for node in (frm, to):
            # Extraer el sub-estado (último segmento).
            sub = node.split(".")[-1] if node else ""
            if sub in HUMAN_GATES:
                gate_transitions[sub] += 1
        if gate:
            for g in HUMAN_GATES:
                if g in gate:
                    gate_transitions[g] += 0  # ensure presence
        tid = str(getattr(e, "task_id", "") or "")
        if tid:
            tasks[tid].add(gate)

    total_baseline = sum(gate_transitions[g] for g in HUMAN_GATES)
    total_post = sum(gate_transitions[g] for g in POST_FUSION_GATES)

    print("Turnos humanos por gate (baseline = 4 gates):")
    for g in sorted(HUMAN_GATES):
        print(f"  {g:16s}: {gate_transitions[g]}")
    print(f"  {'TOTAL (4 gates)':16s}: {total_baseline}")
    print()
    print("Post-fusión (2 gates: PLAN_APPROVAL + REPORT_GATE):")
    for g in sorted(POST_FUSION_GATES):
        print(f"  {g:16s}: {gate_transitions[g]}")
    print(f"  {'TOTAL (2 gates)':16s}: {total_post}")
    print()
    if total_baseline > 0:
        reduccion = (total_baseline - total_post) / total_baseline * 100
        print(f"Reducción estimada: {reduccion:.1f}% ({total_baseline - total_post} turnos menos)")
    else:
        print("Sin transiciones de gates humanos en el trace (sin datos).")

    # Guardar JSON para seguimiento.
    out = {
        "baseline_gates": {g: gate_transitions[g] for g in sorted(HUMAN_GATES)},
        "post_fusion_gates": {g: gate_transitions[g] for g in sorted(POST_FUSION_GATES)},
        "total_baseline": total_baseline,
        "total_post_fusion": total_post,
    }
    out_path = Path(chain_dir) / "data" / "human_turns_report.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        print(f"\nGuardado en {out_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"No se pudo guardar reporte: {exc}")


if __name__ == "__main__":
    main()
