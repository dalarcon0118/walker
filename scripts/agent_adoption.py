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
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fast_mcp.shared_kernel.telemetry import (  # noqa: E402
    metrics_path,
    read_records,
    summarize,
)


HARNESS_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
_NATIVE = ("bash", "edit", "read", "grep", "write", "glob", "todowrite", "question")

#: Antigüedad máxima de la última sesión para dar el ratio por vigente.
HARNESS_MAX_AGE_DAYS = 2.0


def read_harness(
    db_path: str | Path,
    project_path: str = "Active-projects/walker",
    max_age_days: float = HARNESS_MAX_AGE_DAYS,
    now_ms: int | None = None,
) -> dict | None:
    """Llamadas Walker vs nativas por sesión, desde la DB del harness.

    El trace de walker no ve las tools nativas; la DB de opencode sí. Se lee
    read-only y se declara `None` si no está (fail-loud, sin inventar ratio).

    La DB puede estar congelada (otra instancia del harness, sesión en memoria):
    si la última sesión es más vieja que `max_age_days`, el conteo se publica
    pero `native_ratio` queda en `None` con motivo. Un número viejo presentado
    como actual es peor que un N/D (AE-INV-08).
    """
    path = Path(db_path)
    if not path.is_file():
        return None
    placeholders = ",".join("?" for _ in _NATIVE)
    query = f"""
        select s.id,
          sum(case when json_extract(p.data,'$.tool') like 'walker_%' then 1 else 0 end) w,
          sum(case when json_extract(p.data,'$.tool') in ({placeholders}) then 1 else 0 end) n,
          count(*) total,
          max(cast(s.time_created as integer)) latest
        from session s join part p on p.session_id = s.id
        where json_extract(p.data,'$.type')='tool' and s.directory like ?
        group by s.id
        having total > 0
        order by total desc
    """
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        columns = {row[1] for row in db.execute("pragma table_info(session)")}
        if "time_created" not in columns:
            db.close()
            return None
        rows = db.execute(query, (*_NATIVE, f"%{project_path}%")).fetchall()
        db.close()
    except sqlite3.Error:
        return None
    sessions = [
        {"session_id": r[0], "walker": int(r[1] or 0), "native": int(r[2] or 0),
         "total": int(r[3] or 0),
         "ratio": round((r[1] or 0) / r[2], 3) if r[2] else None}
        for r in rows
    ]
    walker = sum(r["walker"] for r in sessions)
    native = sum(r["native"] for r in sessions)
    latest = max((int(r[4]) for r in rows if r[4]), default=None)
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    age_days = (now - latest) / 86_400_000 if latest else None
    stale = age_days is None or age_days > max_age_days
    measured = round(walker / native, 3) if native else None
    latest_iso = (
        datetime.fromtimestamp(latest / 1000, tz=timezone.utc).date().isoformat()
        if latest else ""
    )
    return {
        "db": str(path),
        "sessions": len(sessions),
        "walker_calls": walker,
        "native_calls": native,
        "measured_ratio": measured,
        "native_ratio": None if stale else measured,
        "latest_session": latest_iso,
        "age_days": round(age_days, 1) if age_days is not None else None,
        "stale": stale,
        "stale_reason": (
            f"la última sesión registrada es del {latest_iso} "
            f"({age_days:.1f} días): la DB no refleja la superficie actual"
            if stale and latest_iso else
            ("la DB no expone sesiones con fecha" if stale else "")
        ),
        "per_session": sessions,
    }


def summarize_tasks(records: list[dict]) -> dict:
    """Adopción por (sesión, tarea) desde la telemetría correlacionada.

    Antes el JSONL no guardaba `session_id`: sólo se podía agregar por tool. Con
    la correlación, "¿el agente consultó el brief antes de escribir?" deja de ser
    una opinión. Se agrupa por `(session_id, task_id)` y se mira el primer tool
    de la tarea.
    """
    write_tools = {"submit", "implement_task", "capture"}
    groups: dict[tuple[str, str], dict] = {}
    for record in sorted(records, key=lambda r: str(r.get("ts") or "")):
        sid = str(record.get("session_id") or "")
        if not sid:
            continue
        tid = str(record.get("task_id") or "")
        row = groups.setdefault(
            (sid, tid),
            {"session_id": sid, "task_id": tid, "calls": 0, "briefs": 0,
             "tokens": 0, "first_tool": "", "brief_first": False,
             "brief_before_write": False, "wrote": False},
        )
        tool = str(record.get("tool") or "")
        if row["calls"] == 0:
            row["first_tool"] = tool
            row["brief_first"] = tool == "brief"
        row["calls"] += 1
        row["tokens"] += int(record.get("out_tokens") or 0)
        if tool == "brief":
            row["briefs"] += 1
        if tool in write_tools:
            row["wrote"] = True
            if row["briefs"] > 0:
                row["brief_before_write"] = True

    tasks = sorted(groups.values(), key=lambda r: r["calls"], reverse=True)
    total = len(tasks)
    return {
        "tasks_total": total,
        "tasks_with_brief": sum(1 for t in tasks if t["briefs"]),
        "tasks_brief_first": sum(1 for t in tasks if t["brief_first"]),
        "tasks_brief_before_write": sum(1 for t in tasks if t["brief_before_write"]),
        "tasks": tasks[:20],
        "calls_total": sum(t["calls"] for t in tasks),
    }


def summarize_adoption(records: list[dict], events: list[dict] | None = None,
                       harness: dict | None = None) -> dict:
    """Agrega telemetría + eventos de sesión. Sin números inventados."""
    tools = summarize(records)
    tasks = summarize_tasks(records)
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
            row["task"] = str(data.get("plan_task") or "")
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
        "tasks": tasks,
        "native_ratio": (harness or {}).get("native_ratio"),
        "harness": harness,
        "unavailable": _unavailable(harness, tasks),
    }


def _unavailable(harness: dict | None, tasks: dict | None = None) -> dict[str, str]:
    """Lo no medible, con motivo. Un ratio viejo no se publica como actual."""
    unavailable = {
        "first_pass_tests": "se cuenta por sesión (verified sin verify_failed); "
                            "el intento por tarea no se separa",
    }
    if not (tasks or {}).get("tasks_total"):
        unavailable["tasks"] = "ninguna llamada con `session_id`: no hay adopción por tarea que medir"
    if harness is None:
        unavailable["native_ratio"] = "no se pudo leer la DB del harness"
    elif harness.get("stale"):
        unavailable["native_ratio"] = harness.get("stale_reason") or "DB del harness sin datos recientes"
    return unavailable


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
    tasks = summary.get("tasks") or {}
    if tasks.get("tasks_total"):
        lines.append("")
        lines.append(
            f"tareas: {tasks['tasks_total']} · con brief: {tasks['tasks_with_brief']} "
            f"· brief primero: {tasks['tasks_brief_first']} "
            f"· brief antes de escribir: {tasks['tasks_brief_before_write']}"
        )
    harness = summary.get("harness") or {}
    if harness:
        state = (
            f"NO VIGENTE: {harness['stale_reason']}"
            if harness.get("stale") else "vigente"
        )
        lines.append(
            f"harness: {harness['sessions']} sesiones · walker={harness['walker_calls']} "
            f"nativas={harness['native_calls']} · ratio={harness['native_ratio']} "
            f"({state})"
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
    parser.add_argument("--harness-db", type=Path, default=HARNESS_DB)
    args = parser.parse_args(argv)

    records = read_records(args.path or metrics_path())
    events = [] if args.no_trace else _load_trace_events()
    harness = read_harness(args.harness_db) if not args.no_trace else None
    summary = summarize_adoption(records, events, harness)

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"archivo: {args.path or metrics_path()}")
        print(_table(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
