#!/usr/bin/env python3
"""Adopción del agente por sesión — AGENT-EFFICIENCY §2.6 / AE-INV-08.

Publica lo que el harness SÍ puede medir:
  - llamadas Walker por tool, tokens de salida, p50/p95, empty/error;
  - sesiones con `session_metrics` y verificaciones (del trace).

Lo que NO puede medir hoy se declara `null` con el motivo, en vez de inventar
un número: el ratio Walker/nativas necesita los logs del harness (opencode),
que no están en el trace de walker.

Uso:
    python3 scripts/agent_adoption.py
    python3 scripts/agent_adoption.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fast_mcp.shared_kernel.telemetry import (  # noqa: E402
    metrics_path,
    read_records,
    summarize,
)


def summarize_adoption(records: list[dict], events: list[dict] | None = None) -> dict:
    """Agrega telemetría + eventos de sesión. Sin números inventados."""
    tools = summarize(records)
    sessions: dict[str, dict] = {}
    verifications = 0
    failed = 0
    for event in events or []:
        etype = str(event.get("event_type") or event.get("event") or "")
        data = event.get("data") or {}
        meta = event.get("metadata") or {}
        sid = str(data.get("session_id") or meta.get("session_id") or event.get("session_id") or "")
        if not sid:
            continue
        row = sessions.setdefault(sid, {
            "verifications": 0, "failed": 0, "verified": False,
            "verify_failed": False, "writes": 0, "cost_usd": 0.0,
        })
        if etype == "session_metrics":
            row["verified"] = bool(data.get("verified"))
            row["verify_failed"] = bool(data.get("verify_failed"))
            row["writes"] = int(data.get("writes") or 0)
            row["cost_usd"] = float(data.get("cost_usd") or 0)
        if etype in ("verification", "verify"):
            verifications += 1
            ok = bool(data.get("passed", data.get("ok", False)))
            row["verifications"] += 1
            if not ok:
                failed += 1
                row["failed"] += 1

    first_pass = sum(
        1 for row in sessions.values()
        if row["verified"] and not row["verify_failed"] and not row["failed"]
    )
    return {
        "calls": tools["calls"],
        "out_tokens_total": tools["out_tokens_total"],
        "tools": tools["tools"],
        "sessions": len(sessions),
        "verified_sessions": sum(1 for r in sessions.values() if r["verified"]),
        "writes_total": sum(r["writes"] for r in sessions.values()),
        "cost_usd_total": round(sum(r["cost_usd"] for r in sessions.values()), 6),
        "verifications": verifications,
        "failed_verifications": failed,
        "rework_rate": (failed / verifications) if verifications else None,
        "first_pass_tests": first_pass if sessions else None,
        "native_ratio": None,
        "unavailable": {
            "native_ratio": "el harness (opencode) no publica tool calls nativas en el trace",
            "first_pass_tests": "se cuenta por sesión (verified sin verify_failed); el intento por tarea no se separa",
            "per_session_tools": "la telemetría por tool no guarda session_id",
        },
    }


def _load_trace_events() -> list[dict]:
    try:
        from fast_mcp.features.event_tracing.application.services.trace_factory import (
            build_trace_service,
        )

        service = build_trace_service(str(_ROOT))
        if service is None:
            return []
        return [
            {"event_type": e.event_type, "data": e.data,
             "metadata": e.metadata, "timestamp": e.timestamp}
            for e in service.load_traces(str(_ROOT))
        ]
    except Exception:  # noqa: BLE001 — el reporte no depende del trace
        return []


def _table(summary: dict) -> str:
    lines = [
        f"llamadas Walker: {summary['calls']} · tokens de salida: {summary['out_tokens_total']}",
        f"sesiones: {summary['sessions']} · verificadas: {summary['verified_sessions']} "
        f"· 1er intento: {summary['first_pass_tests']} · writes: {summary['writes_total']}",
        f"verificaciones: {summary['verifications']} (fallidas "
        f"{summary['failed_verifications']}) · costo: ${summary['cost_usd_total']}",
        "",
        f"{'tool':<24}{'n':>5}{'p50':>9}{'p95':>9}{'empty':>8}{'error':>8}{'tok':>9}",
        "-" * 80,
    ]
    for tool, row in summary["tools"].items():
        lines.append(
            f"{tool:<24}{row['n']:>5}{row['p50_ms']:>8}ms{row['p95_ms']:>8}ms"
            f"{row['empty_rate']:>7.0%}{row['error_rate']:>8.0%}"
            f"{row['out_tokens_total']:>9}"
        )
    lines.append("")
    for key, reason in summary["unavailable"].items():
        lines.append(f"N/D {key}: {reason}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Adopción del agente (walker)")
    parser.add_argument("--path", type=Path, default=None, help="JSONL de métricas")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-trace", action="store_true")
    args = parser.parse_args(argv)

    records = read_records(args.path or metrics_path())
    events = [] if args.no_trace else _load_trace_events()
    summary = summarize_adoption(records, events)

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"archivo: {args.path or metrics_path()}")
        print(_table(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
