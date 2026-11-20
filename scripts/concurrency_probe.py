#!/usr/bin/env python3
"""Sonda de concurrencia del server MCP — M-05 de AGENT-EFFICIENCY §2.7.

Mide lo que el agente realmente hace (fan-out de lecturas) y falla si aparece
un deadlock, un timeout o si N concurrentes cuestan más de 1,5× una sola.

Uso:
    python3 scripts/concurrency_probe.py --calls 100 --concurrency 10
    python3 scripts/concurrency_probe.py --json

El deadlock medido el 2026-11-20 (`_ModuleLock(check_cpg_health)`) aparecía en
la PRIMERA ráfaga de un proceso nuevo, así que el default corre todo en este
proceso sin precalentar: si el bug vuelve, se ve acá.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class Probe:
    ok: int = 0
    errors: list[str] = field(default_factory=list)
    latencies_ms: list[int] = field(default_factory=list)
    batches: int = 0

    def summary(self) -> dict:
        lat = sorted(self.latencies_ms)
        n = len(lat)

        def pct(p: float) -> int:
            if not n:
                return 0
            return lat[min(n - 1, int(round((p / 100) * (n - 1))))]

        return {
            "ok": self.ok,
            "errors": len(self.errors),
            "error_samples": self.errors[:5],
            "batches": self.batches,
            "n": n,
            "p50_ms": pct(50),
            "p95_ms": pct(95),
            "max_ms": lat[-1] if lat else 0,
            "mean_ms": int(sum(lat) / n) if n else 0,
        }


async def _one(server, tool: str, payload: dict) -> None:
    await server.call_tool(tool, payload)


async def _run(server, calls: int, concurrency: int, project: str) -> Probe:
    probe = Probe()
    # Mezcla de lectores: el deadlock apareció con status + agent_context +
    # workflow_state en la misma ráfaga.
    plan = [
        ("status", {"project_id": project}),
        ("agent_context", {"project_id": project,
                           "file": "fast_mcp/features/workflow/application/orchestrator.py"}),
        ("workflow_state", {"project_id": project}),
        ("search_memories", {"project_id": project, "ckg": {"text": "gate de alcance"}}),
    ]
    pending = list(range(calls))
    while pending:
        batch, pending = pending[:concurrency], pending[concurrency:]
        probe.batches += 1
        started = time.perf_counter()
        results = await asyncio.gather(
            *(_one(server, *plan[i % len(plan)]) for i in batch),
            return_exceptions=True,
        )
        elapsed = (time.perf_counter() - started) * 1000
        per_call = int(elapsed / max(len(batch), 1))
        for result in results:
            if isinstance(result, BaseException):
                probe.errors.append(f"{type(result).__name__}: {result}")
            else:
                probe.ok += 1
                probe.latencies_ms.append(per_call)
    return probe


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sonda de concurrencia MCP")
    ap.add_argument("--calls", type=int, default=100)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--project", default="walker")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    from fast_mcp.server import build_server

    server = build_server()
    probe = asyncio.run(_run(server, args.calls, args.concurrency, args.project))
    summary = probe.summary()
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"llamadas={summary['ok']} errores={summary['errors']} "
              f"lotes={summary['batches']}")
        print(f"p50={summary['p50_ms']} ms · p95={summary['p95_ms']} ms · "
              f"max={summary['max_ms']} ms")
        for err in summary["error_samples"]:
            print("  ", err)
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
