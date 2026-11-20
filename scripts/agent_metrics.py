#!/usr/bin/env python3
"""Reporte de telemetría por tool — F0 de docs/AGENT-EFFICIENCY.md.

Lee el JSONL que escribe `fast_mcp.shared_kernel.telemetry` y publica lo que el
agente paga por tool: n, p50/p95, tasa de vacío, tasa de error y tokens de
salida.

Uso:
    python3 scripts/agent_metrics.py                 # tabla
    python3 scripts/agent_metrics.py --json          # JSON para CI
    python3 scripts/agent_metrics.py --path X.jsonl  # otro archivo

Criterio F0 (§2.7): los tres lectores de contexto tiene p50/p95 y tasa de
`empty` publicados; sin n y percentil no se reporta nada.
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


def _table(summary: dict) -> str:
    lines = [
        f"llamadas: {summary['calls']} · tokens de salida: {summary['out_tokens_total']}",
        "",
        f"{'tool':<24}{'n':>5}{'p50':>9}{'p95':>9}{'max':>9}{'empty':>8}{'error':>8}{'tok':>9}",
        "-" * 80,
    ]
    for tool, row in summary["tools"].items():
        lines.append(
            f"{tool:<24}{row['n']:>5}{row['p50_ms']:>8}ms{row['p95_ms']:>8}ms"
            f"{row['max_ms']:>8}ms{row['empty_rate']:>7.0%}{row['error_rate']:>8.0%}"
            f"{row['out_tokens_total']:>9}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Telemetría por tool del MCP walker")
    parser.add_argument("--path", type=Path, default=None, help="JSONL de métricas")
    parser.add_argument("--json", action="store_true", help="Salida JSON")
    args = parser.parse_args(argv)

    path = args.path or metrics_path()
    records = read_records(path)
    summary = summarize(records)
    summary["path"] = str(path)

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"archivo: {path}")
        print(_table(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
