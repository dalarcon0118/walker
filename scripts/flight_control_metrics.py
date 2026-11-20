#!/usr/bin/env python3
"""scripts/flight_control_metrics.py — métricas del flight control desde el trace.

Responde una pregunta concreta: ¿el protocolo del plugin ayuda o cuesta turnos?
Lee el índice `trace` (SSOT de los eventos del agente) y agrega lo medible:

  - denials por gate (bind/scope/intake) y en cuántas sesiones aparecen;
  - auto-declaraciones y ampliaciones de alcance (P1.10, sin bloqueo);
  - `close_blocked` (nudges de cierre);
  - verificaciones registradas por la TOOL (P1.11), verdes y rojas;
  - sugerencias del workflow seguidas vs ignoradas.

Las sesiones del harness (`harness-*`, `evict-*`, …) se excluyen por defecto:
sus denials son intencionales y no dicen nada del agente.

Uso:
    python3 scripts/flight_control_metrics.py walker
    python3 scripts/flight_control_metrics.py walker --since 2026-11-20
    python3 scripts/flight_control_metrics.py walker --include-harness --json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# Sesiones que genera el harness o los diagnósticos, no el agente.
_HARNESS_PREFIXES = ("harness", "evict", "smoke", "diag", "probe", "fixture")


def _session(event: dict) -> str:
    return str(event.get("session_id") or event.get("session") or "")


def is_harness(session_id: str) -> bool:
    return session_id.startswith(_HARNESS_PREFIXES)


def summarize(events: list[dict], include_harness: bool = False) -> dict[str, Any]:
    """Agrega eventos normalizados `{event_type, session_id, data}`.

    Función pura: la usan el CLI y los tests (sin tocar el storage).
    """
    sessions: set[str] = set()
    harness_sessions: set[str] = set()
    for event in events:
        sid = _session(event)
        if is_harness(sid):
            harness_sessions.add(sid)
        elif sid:
            sessions.add(sid)

    selected = [e for e in events if include_harness or not is_harness(_session(e))]

    denials: Counter[str] = Counter()
    denial_sessions: set[str] = set()
    expanded_files: set[str] = set()
    expanded_events = 0
    autodeclared = 0
    close_blocked = 0
    verifications = {"green": 0, "red": 0}
    write_evidence = {"events": 0, "files": set()}
    suggestions = {"followed": 0, "ignored": 0}

    for event in selected:
        sid = _session(event)
        kind = str(event.get("event_type") or event.get("type") or "")
        data = event.get("data") or {}

        if kind == "flight_control_denied":
            denials[str(data.get("gate") or "?")] += 1
            denial_sessions.add(sid)
        elif kind == "flight_control_autodeclared":
            autodeclared += 1
        elif kind == "flight_control_scope_expanded":
            expanded_events += 1
            if data.get("file"):
                expanded_files.add(str(data["file"]))
        elif kind == "close_blocked":
            close_blocked += 1
        elif kind == "workflow_state_injected" and data.get("followed") is True:
            suggestions["followed"] += 1
        elif kind == "workflow_suggestion_ignored":
            suggestions["ignored"] += 1
        elif kind == "session_state" and data.get("recorded_by") == "task_tool":
            if data.get("verified"):
                verifications["green"] += 1
            else:
                verifications["red"] += 1
        if kind == "session_state" and data.get("touched_source") == "watcher":
            write_evidence["events"] += 1
            write_evidence["files"].update(str(f) for f in (data.get("touched") or []))

    total_denials = sum(denials.values())
    return {
        "events_considered": len(selected),
        "sessions": {"real": len(sessions), "harness_excluded": len(harness_sessions)},
        "denials": {
            "by_gate": dict(sorted(denials.items())),
            "total": total_denials,
            "sessions_with_denials": len(denial_sessions),
        },
        "autodeclared": autodeclared,
        "scope_expanded": {"events": expanded_events, "files": sorted(expanded_files)},
        "close_blocked": close_blocked,
        "verifications": verifications,
        "write_evidence": {
            "events": write_evidence["events"],
            "files": len(write_evidence["files"]),
        },
        "suggestions": suggestions,
    }


def _load_events(project_id: str, since: str = "", until: str = "") -> list[dict]:
    from fast_mcp.features.event_tracing.application.services.trace_factory import (
        build_trace_service,
    )
    from fast_mcp.shared_kernel.project_support import ProjectService

    paths = ProjectService.resolve_paths(project_id)
    service = build_trace_service(str(paths.data_dir))
    if service is None:
        return []
    out: list[dict] = []
    for event in service.load_traces(str(paths.data_dir)):
        ts = str(getattr(event, "timestamp", "") or "")
        if since and ts < since:
            continue
        if until and ts > until:
            continue
        out.append(
            {
                "event_type": getattr(event, "event_type", ""),
                "session_id": str((event.metadata or {}).get("session_id") or ""),
                "data": getattr(event, "data", {}) or {},
            }
        )
    return out


def _print_table(summary: dict[str, Any]) -> None:
    print(f"eventos: {summary['events_considered']}")
    print(
        f"sesiones: reales={summary['sessions']['real']} "
        f"harness excluidas={summary['sessions']['harness_excluded']}"
    )
    d = summary["denials"]
    print(
        f"denials: total={d['total']} por gate={d['by_gate']} "
        f"sesiones={d['sessions_with_denials']}"
    )
    print(f"auto-declaradas: {summary['autodeclared']}")
    print(
        f"scope ampliado: {summary['scope_expanded']['events']} evento(s), "
        f"{len(summary['scope_expanded']['files'])} archivo(s)"
    )
    print(f"close_blocked: {summary['close_blocked']}")
    v = summary["verifications"]
    print(f"verificaciones por tool: verdes={v['green']} rojas={v['red']}")
    w = summary["write_evidence"]
    print(f"escritura sin hook: {w['events']} evento(s), {w['files']} archivo(s)")
    s = summary["suggestions"]
    print(f"sugerencias: seguidas={s['followed']} ignoradas={s['ignored']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_id", help="proyecto registrado (ej. walker)")
    parser.add_argument("--since", default="", help="ISO date: solo eventos >= (ej. 2026-11-20)")
    parser.add_argument("--until", default="", help="ISO date: solo eventos <= ")
    parser.add_argument("--include-harness", action="store_true")
    parser.add_argument("--json", action="store_true", help="salida JSON")
    args = parser.parse_args(argv)

    try:
        events = _load_events(args.project_id, args.since, args.until)
    except Exception as exc:  # noqa: BLE001 — el CLI reporta el error, no traceback
        print(f"flight_control_metrics: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    summary = summarize(events, include_harness=args.include_harness)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    else:
        _print_table(summary)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
