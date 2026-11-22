#!/usr/bin/env python3
"""Mide hit@5 del CKG para un golden set de preguntas naturales.

Uso: python3 scripts/golden_hit.py [--golden PATH] [--out PATH]
"""
import argparse
import json
import sys
from pathlib import Path

from returns.pipeline import is_successful

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", default="data/golden/queries.json")
    parser.add_argument("--out", default="data/golden/baseline-adr084.json")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
        CkgReadService,
    )

    chain_dir = str(ROOT)
    golden_path = ROOT / args.golden
    out_path = ROOT / args.out
    queries = json.loads(golden_path.read_text())
    rows: list[dict] = []
    hits = 0
    for q in queries:
        result = CkgReadService.gather_context(chain_dir, query=q["question"])
        nodes = result.value_or([]) if is_successful(result) else []
        ids = [str(n.get("id") or n.get("_id") or "") for n in nodes]
        top5 = ids[:5]
        ok = q["expected"] in top5
        if ok:
            hits += 1
        rows.append(
            {
                "id": q["id"],
                "question": q["question"],
                "expected": q["expected"],
                "hit_at_5": ok,
                "top5": top5,
                "rank": (top5.index(q["expected"]) + 1) if ok else None,
            }
        )
    summary = {
        "n": len(queries),
        "hit_at_5": hits,
        "hit_rate": round(hits / len(queries), 3) if queries else 0.0,
        "rows": rows,
    }
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"hit@5 = {hits}/{len(queries)} = {summary['hit_rate']}")
    for r in rows:
        mark = "✓" if r["hit_at_5"] else "✗"
        print(f"  {mark} {r['id']}: {r['expected']}  (rank={r['rank']})")


if __name__ == "__main__":
    main()
