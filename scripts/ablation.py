"""scripts/ablation.py — ¿qué capa de walker mueve el número?

Corre el mismo benchmark N veces variando solo lo que se habilita, y compara
contra el baseline. Sin esto, "tengo memorias" no es evidencia: es una lista de
features.

Brazos:
  baseline    sin MCP (sin tools de walker) y sin inyección
  tools       MCP completo (33 tools), sin inyección automática
  lean        MCP con perfil `agent` (10 tools), sin inyección → aísla la
              superficie de tools del efecto de tener MCP
  full        MCP completo + inyección de CKG y skills
  full-lean   MCP con perfil `agent` + inyección

Uso:
    python3 scripts/ablation.py --repo <repo> --tasks <tasks.json> --k 1
    python3 scripts/ablation.py --repo <repo> --tasks t.json --arms baseline,full --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

WALKER_ROOT = Path(__file__).resolve().parent.parent
RUNNER = WALKER_ROOT / "harness" / "runner.py"

ARMS: dict[str, dict] = {
    "baseline": {
        "no_mcp": True,
        "profile": None,
        "env": {"WALKER_PROMPT_KNOWLEDGE": "0", "WALKER_PROMPT_SKILLS": "0"},
    },
    "tools": {
        "no_mcp": False,
        "profile": "full",
        "env": {"WALKER_PROMPT_KNOWLEDGE": "0", "WALKER_PROMPT_SKILLS": "0"},
    },
    # Aísla la SUPERFICIE de tools: mismo MCP, misma ausencia de inyección,
    # 10 tools en vez de 33.
    "lean": {
        "no_mcp": False,
        "profile": "agent",
        "env": {"WALKER_PROMPT_KNOWLEDGE": "0", "WALKER_PROMPT_SKILLS": "0"},
    },
    "full": {
        "no_mcp": False,
        "profile": "full",
        "env": {"WALKER_PROMPT_KNOWLEDGE": "1", "WALKER_PROMPT_SKILLS": "1"},
    },
    "full-lean": {
        "no_mcp": False,
        "profile": "agent",
        "env": {"WALKER_PROMPT_KNOWLEDGE": "1", "WALKER_PROMPT_SKILLS": "1"},
    },
}


def build_cmd(repo: str, tasks: str, arm: str, k: int, model: str | None,
              out: str) -> list[str]:
    cfg = ARMS[arm]
    cmd = [sys.executable, str(RUNNER), "--repo", repo, "--tasks", tasks,
           "--k", str(k), "--out", out]
    if cfg["no_mcp"]:
        cmd.append("--no-mcp")
    if cfg.get("profile"):
        cmd += ["--tool-profile", cfg["profile"]]
    if model:
        cmd += ["--model", model]
    return cmd


def run_arm(repo: str, tasks: str, arm: str, k: int, model: str | None,
            out: str, dry: bool = False) -> dict:
    cmd = build_cmd(repo, tasks, arm, k, model, out)
    env = {**os.environ, **ARMS[arm]["env"], "WALKER_FLIGHT_CONTROL": "0"}
    if dry:
        print(f"[{arm}] {' '.join(cmd)}")
        return {"arm": arm, "dry": True}
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    stamp = ""
    for line in proc.stdout.splitlines():
        if line.startswith("run "):
            stamp = line.split()[1]
    summary_path = Path(out) / stamp / "summary.json"
    if not summary_path.is_file():
        print(proc.stdout[-800:], file=sys.stderr)
        return {"arm": arm, "error": f"sin summary ({stamp})"}
    summary = json.loads(summary_path.read_text())
    return {"arm": arm, **summary}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--model", default=os.environ.get("HARNESS_MODEL"))
    ap.add_argument("--arms", default="baseline,tools,lean")
    ap.add_argument("--out", default=str(Path.home() / ".local/share/walker/ablation"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = [a for a in arms if a not in ARMS]
    if unknown:
        print(f"brazos desconocidos: {unknown} · válidos: {', '.join(ARMS)}", file=sys.stderr)
        return 2

    results = [run_arm(args.repo, args.tasks, a, args.k, args.model, args.out,
                       dry=args.dry_run) for a in arms]
    if args.dry_run:
        return 0

    base = next((r for r in results if r["arm"] == "baseline" and "tasks" in r), None)
    print(f"\n{'brazo':<10} {'pass':<8} {'tokens_in':<12} {'tokens_out':<12} {'wall_s':<9}")
    for r in results:
        if r.get("error"):
            print(f"{r['arm']:<10} ERROR {r['error']}")
            continue
        tin = (r.get("tokens_in") or {}).get("median")
        tout = (r.get("tokens_out") or {}).get("median")
        delta = ""
        if base and base is not r and tin and (base.get("tokens_in") or {}).get("median"):
            base_tin = base["tokens_in"]["median"]
            delta = f" ({tin / base_tin - 1:+.0%} in)"
        print(f"{r['arm']:<10} {r.get('pass')}/{r.get('tasks'):<5} "
              f"{(tin if tin else '?'):<12} {(tout if tout else '?'):<12} "
              f"{r.get('wall_s')} {delta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
