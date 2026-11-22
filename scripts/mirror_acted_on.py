#!/usr/bin/env python3
"""Mide `mirror_acted_on` para una sesión: ¿leer el espejo cambió la acción?

Definición operacional (pre-registrada en ADR-084):
  Un "actuado" = una lectura de `brief` cuyo `self.gaps` era no vacío, y en
  los 3 eventos de tool siguientes aparece el remedio del gap (run_tests /
  verify_predicates para `no_verify`/`no_evidence`; session_plan/add para
  `scope_expanded`; submit para `denials`).

  mirror_acted_on = actuados / lecturas_con_gap   (umbral >= 30%)

  Si no hay lecturas con gap, se reporta como "sin datos" (no como 100%).

Uso: python3 scripts/mirror_acted_on.py <session_id> [--chain-dir PATH]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REMEDY_MAP = {
    "no_verify": {"run_tests", "verify_predicates"},
    "no_evidence": {"run_tests", "verify_predicates"},
    "close_blocked": {"run_tests", "verify_predicates"},
    "denials": {"session_bind", "session_plan", "implement_task"},
    "scope_expanded": {"session_plan"},
    "unverified_criteria": {"implement_task", "verify_predicates"},
}


def main() -> None:
    session_id = sys.argv[1] if len(sys.argv) > 1 else ""
    chain_dir = str(ROOT)
    if "--chain-dir" in sys.argv:
        idx = sys.argv.index("--chain-dir")
        chain_dir = sys.argv[idx + 1]

    from fast_mcp.features.event_tracing.application.services.trace_factory import (
        build_trace_service,
    )

    service = build_trace_service(chain_dir)
    if service is None:
        print("ERROR: no se pudo construir el TraceService")
        sys.exit(1)
    events = [
        e for e in service.load_traces(chain_dir)
        if str((getattr(e, "metadata", None) or {}).get("session_id", "")) == session_id
    ]
    if not events:
        print(f"Sin eventos para la sesión {session_id}")
        sys.exit(0)

    # Ordenar por timestamp.
    events.sort(key=lambda e: str(getattr(e, "timestamp", "") or ""))

    # Identificar lecturas de brief (tool invocation o brief_injected).
    brief_reads: list[tuple[int, dict]] = []
    for i, e in enumerate(events):
        et = str(getattr(e, "event_type", "") or "")
        data = getattr(e, "data", None) or {}
        if not isinstance(data, dict):
            data = {}
        if et == "brief_injected" or (et == "SUBMIT" and "brief" in str(data.get("tool", "")).lower()):
            # El self state viene del evento si está disponible.
            self_state = data.get("self") or {}
            gaps = self_state.get("gaps") or data.get("gaps") or []
            if gaps:
                brief_reads.append((i, {"gaps": gaps, "ts": str(getattr(e, "timestamp", ""))}))

    if not brief_reads:
        print(f"Sesión {session_id}: 0 lecturas de brief con gaps. mirror_acted_on = sin datos.")
        return

    acted = 0
    for idx, info in brief_reads:
        gap_codes = set()
        for g in info["gaps"]:
            code = g.get("code") if isinstance(g, dict) else None
            if code:
                gap_codes.add(code)
        if not gap_codes:
            continue
        # Remedios esperados para los gaps de esta lectura.
        expected_tools: set[str] = set()
        for code in gap_codes:
            expected_tools |= REMEDY_MAP.get(code, set())
        # Mirar los próximos 3 eventos de tool.
        next_tools: set[str] = set()
        count = 0
        for j in range(idx + 1, len(events)):
            et = str(getattr(events[j], "event_type", "") or "")
            data = getattr(events[j], "data", None) or {}
            if not isinstance(data, dict):
                data = {}
            tool = str(data.get("tool_name") or data.get("tool") or "").lower()
            if tool:
                next_tools.add(tool.split("/")[-1])
                count += 1
            if et in ("close_blocked", "flight_control_denied", "flight_control_scope_expanded", "run_tests", "SUBMIT"):
                next_tools.add(et.lower())
                count += 1
            if count >= 5:
                break
        if expected_tools & next_tools:
            acted += 1

    rate = acted / len(brief_reads) if brief_reads else 0.0
    print(f"Sesión {session_id}:")
    print(f"  lecturas de brief con gap: {len(brief_reads)}")
    print(f"  actuados (remedio en ≤3 turnos): {acted}")
    print(f"  mirror_acted_on = {rate:.2f}  (umbral >= 0.30)")
    if rate < 0.30 and len(brief_reads) >= 3:
        print("  → por debajo del umbral: degradar el espejo a on-demand (ADR-084)")
    elif len(brief_reads) < 3:
        print("  → muestra insuficiente (<3 lecturas con gap): no se decide todavía")
    else:
        print("  → por encima del umbral: el espejo se mantiene")


if __name__ == "__main__":
    main()
