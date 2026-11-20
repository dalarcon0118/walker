#!/usr/bin/env python3
"""Golden set de consultas — F0 de docs/AGENT-EFFICIENCY.md §2.7.

Mide lo único que importa para la memoria de decisiones: *¿el retrieval devuelve
la decisión correcta cuando se le pregunta en lenguaje natural?*

Método (el mismo camino que usa el agente, sin MCP): una invocación de CLI por
query a `agent_context(text=...)`; hit@5 si el id esperado aparece en los
primeros 5 resultados del CKG.

Uso:
    python3 scripts/golden_queries.py                  # tabla
    python3 scripts/golden_queries.py --json           # JSON
    python3 scripts/golden_queries.py --write-baseline data/golden/baseline.json
    python3 scripts/golden_queries.py --only q19 --json

El set se congela: cambiar queries o expected requiere evidencia (ver §4 del
documento). Este script no arregla nada: mide.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SET = ROOT / "data" / "golden" / "agent_queries.json"
CLI = ROOT / "bin" / "walker3"


def _run_query(project: str, query: str, timeout: float) -> tuple[list[dict], int, str]:
    """Devuelve (resultados_ckg, latencia_ms, error)."""
    started = time.perf_counter()
    payload = json.dumps({"text": query}, ensure_ascii=False)
    try:
        completed = subprocess.run(
            [str(CLI), f"{project}/agent_context", payload],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return [], int((time.perf_counter() - started) * 1000), "timeout"
    latency = int((time.perf_counter() - started) * 1000)
    if completed.returncode != 0:
        return [], latency, f"rc={completed.returncode}"
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return [], latency, "json inválido"
    results = (data.get("ckg") or {}).get("results") or []
    return results, latency, ""


def _blob(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False).lower()


def evaluate(entry: dict, project: str, timeout: float, top: int = 5) -> dict:
    results, latency_ms, error = _run_query(project, str(entry["query"]), timeout)
    expected = [str(x).lower() for x in entry.get("expected", [])]
    hit_rank = 0
    for position, result in enumerate(results[:top], start=1):
        blob = _blob(result)
        if any(item in blob for item in expected):
            hit_rank = position
            break
    first_adr = ""
    if results:
        metadata = results[0].get("metadata") or {}
        first_adr = str(metadata.get("adr") or metadata.get("source") or "")[:48]
    return {
        "id": entry.get("id"),
        "query": entry["query"],
        "expected": entry.get("expected", []),
        "hit": hit_rank > 0,
        "hit_rank": hit_rank,
        "results": len(results),
        "first_adr": first_adr,
        "latency_ms": latency_ms,
        "error": error,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="hit@5 del golden set")
    parser.add_argument("--set", type=Path, default=DEFAULT_SET)
    parser.add_argument("--project", default="walker")
    parser.add_argument("--timeout", type=float, default=40.0)
    parser.add_argument("--only", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-baseline", type=Path, default=None)
    args = parser.parse_args(argv)

    payload = json.loads(args.set.read_text(encoding="utf-8"))
    queries = payload.get("queries", [])
    if args.only:
        queries = [q for q in queries if q.get("id") == args.only]

    rows = [evaluate(entry, args.project, args.timeout) for entry in queries]
    hits = sum(1 for row in rows if row["hit"])
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": args.project,
        "set": str(args.set),
        "total": len(rows),
        "hits": hits,
        "hit_rate": round(hits / len(rows), 3) if rows else 0.0,
        "mean_latency_ms": int(sum(r["latency_ms"] for r in rows) / len(rows)) if rows else 0,
        "rows": rows,
    }

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"golden set: {args.set} · proyecto: {args.project}")
        print(f"{'id':<5}{'hit':<5}{'rank':<6}{'n':<4}{'ms':<7}query")
        print("-" * 92)
        for row in rows:
            print(
                f"{row['id']:<5}{'SI' if row['hit'] else 'no':<5}{row['hit_rank'] or '-':<6}"
                f"{row['results']:<4}{row['latency_ms']:<7}{row['query'][:56]}"
            )
        print("-" * 92)
        print(f"hit@5: {hits}/{len(rows)} ({summary['hit_rate']:.0%}) · latencia media {summary['mean_latency_ms']} ms")

    if args.write_baseline:
        args.write_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.write_baseline.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"baseline escrito: {args.write_baseline}", file=sys.stderr)

    return 0 if hits == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
