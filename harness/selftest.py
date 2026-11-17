#!/usr/bin/env python3
"""Selftest de la integración harness ↔ walker. No llama al modelo.

Verifica por la API MCP lo que el harness usa de verdad y lo que históricamente
se rompió: registro del proyecto, `chain_dir` (paths de memorias), schemas de
los índices, discriminación de `verify_predicates`, y el ciclo
`implement_task → trace_push → capture → submit`.

Uso:
    python harness/selftest.py --project backend-microservices
    python harness/selftest.py --project backend-microservices --json

Sale con 0 si todo lo requerido pasa, 1 si algo falla.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from walker_client import WalkerClient  # noqa: E402

REQUIRED_INDEXES = ("delta", "ckg", "trace")


def _assert(cond: object, message: str) -> str:
    if not cond:
        raise AssertionError(message)
    return "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--expected-path", default=None, help="repo_path esperado")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--read-only", action="store_true",
                    help="no muta estado: salta implement_task, trace_push, capture y submit")
    args = ap.parse_args()

    checks: list[dict] = []

    def check(name: str, fn, required: bool = True) -> object:
        started = time.time()
        try:
            detail, ok = fn(), True
        except Exception as exc:
            detail, ok = f"{type(exc).__name__}: {exc}", False
        elapsed = round((time.time() - started) * 1000)
        checks.append({"check": name, "ok": ok, "ms": elapsed, "detail": str(detail)[:160], "required": required})
        if not args.json:
            print(f"  {'OK  ' if ok else 'FAIL'} {name:<30} {elapsed:>5}ms  {str(detail)[:70]}")
        return detail if ok else None

    tmp = Path(tempfile.mkdtemp(prefix="walker-selftest-"))
    (tmp / "present.txt").write_text("ok\n")
    task_id = f"selftest-{int(time.time())}"

    with WalkerClient() as w:
        projects = check("list_projects", lambda: w.call("list_projects", {}).get("projects") or [])
        check("proyecto registrado",
              lambda: _assert(isinstance(projects, list) and args.project in projects,
                              f"{args.project} no está en {projects}"))

        health = check("status", lambda: w.call("status", {"project_id": args.project}))
        repo_path = (health or {}).get("project", {}).get("repo_path") if isinstance(health, dict) else None
        check("project.repo_path existe",
              lambda: _assert(repo_path and Path(repo_path).is_dir(), f"no existe: {repo_path}"))
        if args.expected_path:
            check("repo_path == esperado",
                  lambda: _assert(repo_path == args.expected_path, f"{repo_path} != {args.expected_path}"))
        watcher = (health or {}).get("watcher", {}).get("timestamp") if isinstance(health, dict) else None
        check("watcher timestamp", lambda: _assert(bool(watcher), "sin timestamp"), required=False)

        for index in REQUIRED_INDEXES:
            check(f"index schema:{index}",
                  lambda i=index: str(w.call("memory_schema", {"project_id": args.project, "index": i}))[:90])

        check("sprint activo", lambda: w.call("resolve_project_sprint", {"project_id": args.project}),
              required=False)

        def verify(path: str) -> bool:
            data = w.call("verify_predicates", {
                "project_id": args.project, "task_id": task_id, "project_root": str(tmp),
                "predicates": [{"type": "file_exists", "path": path}],
            }).get("data") or {}
            return data.get("all_satisfied") is True

        check("verify: presente → true", lambda: _assert(verify("present.txt"), "debía dar true"))
        check("verify: ausente → false", lambda: _assert(not verify("missing.txt"), "debía dar false"))

        if not args.read_only:
            check("implement_task",
                  lambda: _assert(bool((w.call("implement_task", {
                      "project_id": args.project, "task_id": task_id,
                      "objective": "harness selftest",
                  }).get("data") or {}).get("task_id")), "sin task_id"))

            check("trace_push",
                  lambda: _assert(bool((w.call("trace_push", {
                      "event_type": "harness.selftest", "source": "harness",
                      "task_id": task_id, "project_id": args.project, "data": {"ok": True},
                  }).get("data") or {}).get("event_id")), "sin event_id"))

            captured = check("capture", lambda: w.call("capture", {
                "project_id": args.project, "task_id": task_id, "evidence": "selftest",
            }))
            chain_dir = (captured or {}).get("data", {}).get("chain_dir") if isinstance(captured, dict) else None
            check("capture.chain_dir existe",
                  lambda: _assert(chain_dir and Path(chain_dir).is_dir(), f"no existe: {chain_dir}"))
            check("chain_dir == repo_path",
                  lambda: _assert(chain_dir == repo_path, f"{chain_dir} != {repo_path}"), required=False)

            def submit_check() -> str:
                response = w.call("submit", {
                    "project_id": args.project, "decision": "harness selftest", "evidence": task_id,
                }) or {}
                return _assert(response.get("ok") is True, f"walker: {json.dumps(response, ensure_ascii=False)[:140]}")
            check("submit", submit_check)


    shutil.rmtree(tmp, ignore_errors=True)

    failed = [c for c in checks if c["required"] and not c["ok"]]
    if args.json:
        print(json.dumps({"project": args.project, "checks": checks, "failed": len(failed),
                          "ok": not failed}, indent=2, ensure_ascii=False))
    else:
        total = sum(1 for c in checks if c["required"])
        print(f"\n{total - len(failed)}/{total} checks requeridos OK"
              + (f" · {len(failed)} FALLARON" if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
