#!/usr/bin/env python3
"""Shim: enforce.py — delega en features (ADR-WALKER-010).

Implementación real migrada a features/engine/domain/enforce.py (servicio PURO).
Este shim conserva la firma legacy ``enforce_task(chain_dir, task_id, force)``
que usa to_clean_next/_internal/advance.py: lee plan.json/progress.json,
verifica el checksum (STRICT) y delega en el servicio de dominio.
"""
from __future__ import annotations

import json
import os
import sys

from features.engine.domain.enforce import (  # noqa: E402,F401
    enforce_task as _pure_enforce_task,
    get_next_action,
    get_project_id,
    find_backlog_path,
    CURVATURE_BUDGET_DEFAULT,
)

# ── Compatibilidad con la firma legacy ─────────────────────────


def load_json(path: str) -> dict | None:
    """Load a JSON file with error handling (checksum STRICT para progress.json)."""
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        sys.stderr.write(f"❌ JSON inválido en {path}: {e}\n")
        sys.exit(2)

    if os.path.basename(path) == "progress.json":
        try:
            from features.code_analysis.application.deep_analysis import verify_checksum

            if not verify_checksum(data):
                sys.stderr.write(
                    "❌ ERROR: progress.json manipulado externamente — checksum no coincide\n"
                )
                sys.stderr.write(f"   Archivo: {path}\n")
                sys.exit(2)
        except ImportError:
            sys.stderr.write(
                "⚠️  verify_checksum no disponible — saltando verificación de integridad\n"
            )

    return data


def find_project_root() -> str:
    """Find the project root (where .cognitive/ lives)."""
    cwd = os.getcwd()
    for _ in range(5):
        if os.path.exists(os.path.join(cwd, ".cognitive")):
            return cwd
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent
    return os.getcwd()


def enforce_task(chain_dir, task_id, force=False, json_out=False):
    """Firma legacy: (chain_dir, task_id, force). Lee archivos y delega en features."""
    reasons = []
    warnings = []

    plan_path = os.path.join(chain_dir, "plan.json")
    progress_path = os.path.join(chain_dir, "progress.json")

    plan = load_json(plan_path)
    progress = load_json(progress_path)
    if plan is None or progress is None:
        return True, reasons, warnings

    project_root = find_project_root()
    budget = float(os.environ.get("DELTA_CURVATURE_BUDGET", CURVATURE_BUDGET_DEFAULT))

    ok, reasons, warnings = _pure_enforce_task(
        plan,
        progress,
        task_id,
        force=force,
        curvature_budget=budget,
        project_root=project_root,
    )

    if json_out:
        print(
            json.dumps(
                {
                    "ok": ok,
                    "reasons": reasons,
                    "warnings": warnings,
                    "overridden": bool(force and reasons),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        if reasons:
            if force:
                print("⚠️  ENFORCEMENT OVERRIDDEN via --force:")
                for r in reasons:
                    print(f"   • [{r['rule']}] {r['message']}")
            else:
                print("🚫 [ENFORCEMENT] Avance bloqueado por desviación del goal:")
                for r in reasons:
                    print(f"   • [{r['rule']}] {r['message']}")
                print(
                    "   ➡️  Para override explícito: delta-advance.py <chain> <task> --force"
                )
        for w in warnings:
            print(f"   ⚠️  {w}")
        if ok and not reasons and not warnings:
            print("✅ [ENFORCEMENT] Tarea alineada con el goal del sprint.")

    return ok, reasons, warnings


def run_selftest():
    """Smoke test equivalente al legacy (delega en la lógica migrada)."""
    import tempfile

    tmp = tempfile.mkdtemp(prefix="delta-enforce-")
    chain = os.path.join(tmp, "selftest-chain")
    os.makedirs(chain, exist_ok=True)

    plan = {
        "plan": {
            "goal": {"predicates": {"must_exist": ["P1", "P2"], "must_not_exist": [], "invariants": []}},
            "tasks": [
                {"task_id": "TASK-001", "risks": {"curvature": 0.10}, "validation": {"predicates": ["P1"]}},
                {"task_id": "TASK-002", "risks": {"curvature": 0.80}, "validation": {"predicates": ["P2"]}},
            ],
        }
    }
    progress = {
        "progress": {
            "meta": {"chain_id": "selftest-chain", "status": "active"},
            "current": {
                "task_states": {"TASK-001": "pending", "TASK-002": "pending"},
                "next_action": {"task_id": "TASK-001"},
            },
            "trail": [],
        }
    }
    with open(os.path.join(chain, "plan.json"), "w") as f:
        json.dump(plan, f)
    with open(os.path.join(chain, "progress.json"), "w") as f:
        json.dump(progress, f)

    failures = []
    ok, _, _ = enforce_task(chain, "TASK-001")
    if not ok:
        failures.append("Test1: TASK-001 (next_action) debería pasar")
    ok, _, _ = enforce_task(chain, "TASK-002")
    if ok:
        failures.append("Test2: TASK-002 (fuera de orden) debería bloquear")
    ok, _, _ = enforce_task(chain, "TASK-002", force=True)
    if not ok:
        failures.append("Test3: TASK-002 --force debería pasar")

    if failures:
        print("❌ SELFTEST FALLÓ:")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("✅ SELFTEST delta-enforce: 3/3 pasaron (next_action, orden, --force)")
    return 0


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]

    if "--selftest" in flags:
        sys.exit(run_selftest())

    if len(args) != 2:
        print("Uso: python3 delta-enforce.py <chain_dir> <task_id> [--force] [--json]")
        print("     python3 delta-enforce.py --selftest")
        sys.exit(2)

    chain_dir, task_id = args
    force = "--force" in flags
    json_out = "--json" in flags

    if not os.path.isdir(chain_dir):
        sys.stderr.write(f"❌ Directorio no encontrado: {chain_dir}\n")
        sys.exit(2)

    ok, _, _ = enforce_task(chain_dir, task_id, force=force, json_out=json_out)
    if force and not ok:
        print("⚠️  ENFORCEMENT OVERRIDDEN via --force")
        sys.exit(0)
    sys.exit(0 if ok else 1)


__all__ = [
    "enforce_task",
    "get_next_action",
    "get_project_id",
    "find_backlog_path",
    "load_json",
    "find_project_root",
    "run_selftest",
    "main",
    "CURVATURE_BUDGET_DEFAULT",
]

if __name__ == "__main__":
    main()
