#!/usr/bin/env python3
"""Auditoría de memorias de walker: ¿se llenaron y se pueden consultar?

Compara los conteos por índice (docs/edges/types) antes y después de una
corrida, y prueba si los eventos del harness son recuperables por la API.

Uso:
    # antes
    python harness/memory_audit.py --project P --snapshot > /tmp/before.json
    # ... correr el harness ...
    python harness/memory_audit.py --project P --compare /tmp/before.json

Sale con 1 si el índice `trace` no creció o si un evento quedó no consultable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from walker_client import WalkerClient  # noqa: E402

INDEXES = ("delta", "ckg", "cpg", "trace")


def snapshot(w: WalkerClient, project: str) -> dict:
    out: dict = {}
    for index in INDEXES:
        raw = w.call("memory_schema", {"project_id": project, "index": index})
        schema = raw.get("schema", raw) if isinstance(raw, dict) else {}
        out[index] = {
            "docs": schema.get("total_docs", 0),
            "edges": schema.get("total_edges", 0),
            "types": schema.get("types", {}),
        }
    return out


def queryable(w: WalkerClient, project: str, task_id: str) -> int:
    """¿El evento del harness se puede leer por la API? Devuelve los hits."""
    result = w.call("search_memories", {
        "project_id": project,
        "_payload": {"trace": {"task_id": task_id}},
    })
    trace = ((result.get("responses") or {}).get("trace") or {})
    return int((trace.get("hits") or {}).get("total") or 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--snapshot", action="store_true", help="imprime el estado actual como JSON")
    ap.add_argument("--compare", default=None, help="compara contra un snapshot previo")
    ap.add_argument("--task-id", default=None, help="verifica que ese task_id sea consultable")
    args = ap.parse_args()

    with WalkerClient() as w:
        current = snapshot(w, args.project)
        if args.snapshot and not args.compare:
            print(json.dumps(current, indent=2, ensure_ascii=False))
            return 0

        print(f"{'índice':<7} {'antes':>6} {'ahora':>6} {'Δdocs':>6} {'edges':>6}  tipos")
        for index in INDEXES:
            before = 0
            if args.compare:
                before = json.loads(Path(args.compare).read_text()).get(index, {}).get("docs", 0)
            now = current[index]["docs"]
            flag = "" if index != "cpg" or now > 0 else "  ← VACÍO"
            print(f"{index:<7} {before:>6} {now:>6} {now - before:>+6} {current[index]['edges']:>6}  "
                  f"{current[index]['types']}{flag}")

        failed = False
        if args.compare:
            delta_trace = current["trace"]["docs"] - json.loads(Path(args.compare).read_text()).get("trace", {}).get("docs", 0)
            print(f"\ntrace: {delta_trace:+d} documentos en la corrida")
            if delta_trace <= 0:
                print("  ! trace no creció: la ingesta no está escribiendo", file=sys.stderr)
                failed = True

        if args.task_id:
            hits = queryable(w, args.project, args.task_id)
            print(f"consultable por task_id={args.task_id}: {hits} hit(s)")
            if hits == 0:
                print("  ! el evento no es recuperable por la API (write-only)", file=sys.stderr)
                failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
