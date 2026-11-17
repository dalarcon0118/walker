"""scripts/hooks_runner.py — punto de entrada de los hooks de walker.

Uso:
    python3 scripts/hooks_runner.py commit
    python3 scripts/hooks_runner.py task.after --payload '{"task": "run_tests"}'
    echo '{"task": "x"}' | python3 scripts/hooks_runner.py task.after --repo .
    python3 scripts/hooks_runner.py commit --list        # qué hooks correrían

Corre los hooks globales (`<walker>/config/hooks/hooks.json`) y del proyecto
(`<repo>/.cognitive/hooks/hooks.json`), con el payload JSON por stdin. Sale 1
solo si un hook marcado `blocking` falló (pensado para eventos `before`); en
cualquier otro caso sale 0 para no romper al host.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WALKER_ROOT = Path(__file__).resolve().parent.parent
if str(WALKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WALKER_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("event", help="commit | cpg.indexed | task.after | ...")
    ap.add_argument("--payload", default=None, help="JSON del evento (si no, stdin)")
    ap.add_argument("--repo", default=None, help="repo del proyecto (def: cwd)")
    ap.add_argument("--list", action="store_true", help="solo lista, no ejecuta")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    from fast_mcp.shared_kernel.hooks import HookBus

    repo = Path(args.repo or Path.cwd()).resolve()
    bus = HookBus.load(repo, WALKER_ROOT)

    payload: dict = {}
    raw = args.payload
    if raw is None and not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
    if raw:
        try:
            parsed = json.loads(raw)
            payload = parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            payload = {"raw": raw}

    specs = bus.hooks_for(args.event)
    if args.list:
        for spec in specs:
            print(f"  {spec.id:<28} [{spec.source}] timeout={spec.timeout_s}s "
                  f"blocking={spec.blocking} · {spec.command}")
        if not specs:
            print(f"  (sin hooks para '{args.event}')")
        return 0

    results = bus.emit(args.event, payload)
    if not args.quiet:
        if not results:
            print(f"hooks: sin suscriptores para '{args.event}'")
        for res in results:
            state = "ok" if res.ok else ("TIMEOUT" if res.timed_out else "falló")
            detail = "" if res.ok else f" · {(res.error or res.stderr).strip()[:160]}"
            print(f"  {res.hook_id:<28} {state} rc={res.rc} {res.duration_s}s{detail}")
    return 1 if bus.blocked(results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
# probe
