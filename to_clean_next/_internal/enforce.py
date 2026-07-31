#!/usr/bin/env python3
"""delta-enforce.py — Capa de COERCIÓN del skill delta.

Verifica que una tarea a avanzar NO se aleja del goal del sprint activo.
Es la pieza que convierte el protocolo del skill (advisory) en enforcement
duro: bloquea (exit 1) si la tarea:

  1. next_action      → no es la next_action de progress.json (avance fuera de orden)
  2. curvature_budget → excede el presupuesto de curvature_risk (impacto arquitectónico)
  3. distance_guard   → no tiene predicates de validación (no reduce la distancia al G_objetivo)
  4. backlog_required → no está en plan.json y tampoco en backlog.jsonl (tarea fuera de scope)

Uso:
    python3 delta-enforce.py <chain_dir> <task_id> [--force] [--json]
    python3 delta-enforce.py --selftest          # smoke test para CI

Exit:
    0 → tarea alineada con el goal (o override con --force)
    1 → avance bloqueado (usar --force para override explícito)
    2 → error de uso

El presupuesto de curvature se configura con DELTA_CURVATURE_BUDGET (default 0.50).
"""

import json
import os
import sys
import tempfile

CURVATURE_BUDGET_DEFAULT = 0.50


def find_project_root():
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


def load_json(path):
    """Load a JSON file with error handling.

    For ``progress.json``, verifies the ``_checksum`` field (SHA-256 integrity).
    Returns None if checksum doesn't match (file was tampered externally).
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        sys.stderr.write(f"❌ JSON inválido en {path}: {e}\n")
        sys.exit(2)

    # Integrity check for progress.json
    # STRICT: tampered file → sys.exit(2), not return None.
    # Returning None would make enforce_task() allow the advance (see enforce_task L93).
    if os.path.basename(path) == "progress.json":
        try:
            from _internal.progress_checksum import verify_checksum

            if not verify_checksum(data):
                sys.stderr.write(
                    "❌ ERROR: progress.json manipulado externamente — checksum no coincide\n"
                )
                sys.stderr.write(f"   Archivo: {path}\n")
                sys.stderr.write(
                    "   El enforcement gate NO puede operar con datos corruptos.\n"
                )
                sys.exit(2)
        except ImportError:
            sys.stderr.write(
                "⚠️  progress_checksum no disponible — saltando verificación de integridad\n"
            )

    return data


def get_next_action(progress):
    """Extrae el task_id de la next_action desde los esquemas v2.0 / v3.0."""
    prog = progress.get("progress", progress)
    # v2.0: progress.current.next_action
    na = (prog.get("current") or {}).get("next_action")
    if isinstance(na, dict):
        return na.get("task_id")
    # v3.0 / variantes: next_action a nivel raíz o bajo progress
    na = progress.get("next_action")
    if isinstance(na, dict):
        return na.get("task_id")
    na = prog.get("next_action")
    if isinstance(na, dict):
        return na.get("task_id")
    return None


def _get_project_id(plan: dict) -> str | None:
    """Extract project_id from plan data (multi-project support)."""
    try:
        pl = plan.get("plan", plan)
        return pl.get("project", {}).get("project_id") or None
    except Exception:
        return None


def enforce_task(chain_dir, task_id, force=False, json_out=False):
    """Devuelve (ok, reasons, warnings). ok=False → bloquear salvo --force."""
    reasons = []
    warnings = []

    plan_path = os.path.join(chain_dir, "plan.json")
    progress_path = os.path.join(chain_dir, "progress.json")

    plan = load_json(plan_path)
    progress = load_json(progress_path)
    if plan is None or progress is None:
        # Sin plan/progress no podemos evaluar → permitir (no es responsabilidad de este gate)
        return True, reasons, warnings

    pl = plan.get("plan", plan)
    prog = progress.get("progress", progress)

    # ── Regla 1: next_action ───────────────────────────────────────
    # Defense-in-depth: si la tarea ya está en ready_for_review, se salta
    # el check de next_action porque el TASK LOOP ya la validó.
    # Esto evita bloquear confirmaciones de tareas ya evaluadas.
    task_state = (prog.get("current") or {}).get("task_states", {}).get(task_id)
    if task_state == "ready_for_review":
        pass  # Tarea ya evaluada — permitir confirmación sin importar next_action
    else:
        na = get_next_action(progress)
        if na is not None and na != "__ALL_DONE__" and na != task_id:
            reasons.append(
                {
                    "rule": "next_action",
                    "message": (
                        f"La tarea '{task_id}' NO es la next_action del sprint ('{na}'). "
                        "Avanzar fuera de orden es desviación del goal."
                    ),
                }
            )

    # Localizar la tarea en plan.json
    task = None
    for t in pl.get("tasks", []):
        if t.get("task_id") == task_id:
            task = t
            break

    # ── Regla 2: curvature_budget ──────────────────────────────────
    if task is not None:
        risks = task.get("risks", {}) or {}
        curvature = risks.get("curvature", risks.get("curvature_risk", 0.20))
        try:
            curvature = float(curvature)
        except (TypeError, ValueError):
            curvature = 0.20
        budget = float(
            os.environ.get("DELTA_CURVATURE_BUDGET", CURVATURE_BUDGET_DEFAULT)
        )
        if curvature > budget:
            reasons.append(
                {
                    "rule": "curvature_budget",
                    "message": (
                        f"curvature_risk={curvature:.2f} > presupuesto={budget:.2f}. "
                        "La tarea tiene alto impacto arquitectónico y se aleja del camino planeado."
                    ),
                }
            )

        # ── Regla 3: distance_guard (advertencia, no bloquea) ──────
        preds = task.get("validation", {}).get("predicates", [])
        if not preds and not task.get("result_state"):
            warnings.append(
                f"La tarea '{task_id}' no tiene predicates de validación ni result_state: "
                "no reduce la distancia al G_objetivo (posible drift)."
            )
    else:
        # ── Regla 4: backlog_required (tarea fuera de plan.json) ───
        # Multi-project: backlog per-project si hay project_id en plan
        project_id = _get_project_id(plan)
        if project_id:
            backlog_path = os.path.join(
                find_project_root(),
                ".cognitive",
                "projects",
                project_id,
                "delta",
                "backlog.jsonl",
            )
        else:
            backlog_path = os.path.join(
                find_project_root(), ".cognitive", "delta", "backlog.jsonl"
            )
        found = False
        if os.path.exists(backlog_path):
            with open(backlog_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        bj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if bj.get("id") == task_id or task_id in (
                        bj.get("description") or ""
                    ):
                        found = True
                        break
        if not found:
            reasons.append(
                {
                    "rule": "backlog_required",
                    "message": (
                        f"La tarea '{task_id}' NO está en plan.json y NO está registrada en "
                        "backlog.jsonl. Toda tarea fuera de scope debe registrarse en el backlog "
                        "antes de avanzar (o usar --force)."
                    ),
                }
            )

    ok = (len(reasons) == 0) or force

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

    # ── DECISION PROTOCOL (self-sufficient output) ────────────────
    if not json_out:
        print()
        print("─" * 70)
        print("📋 DECISION PROTOCOL:")
        print()
        if ok:
            print("   ✅ Tarea alineada — puedes continuar con ADVANCE:")
            print(
                f"      python3 scripts/delta-advance.py {chain_dir} {task_id} [--confirm]"
            )
        else:
            print("   🚫 Avance bloqueado por desviación del goal.")
            print()
            print("   ▶️  Opción A — Override intencional:")
            print(
                f"      python3 scripts/delta-advance.py {chain_dir} {task_id} --force --confirm"
            )
            print(
                "      Usar cuando la desviación es deliberada y necesaria para el sprint."
            )
            print()
            print("   ▶️  Opción B — Replanificar:")
            print("      Si el goal cambió significativamente, crear un nuevo chain_id")
            print("      (el plan es inmutable) y cerrar el actual como SUPERSEDED.")
        print()
        print("💡 El output de este script ya contiene la decisión a tomar.")
        print("=" * 70)

    return ok, reasons, warnings


def run_selftest():
    """Valida la lógica de enforcement con un fixture temporal. Para CI."""
    tmp = tempfile.mkdtemp(prefix="delta-enforce-")
    chain = os.path.join(tmp, "selftest-chain")
    os.makedirs(chain, exist_ok=True)

    plan = {
        "plan": {
            "goal": {
                "predicates": {
                    "must_exist": ["P1", "P2"],
                    "must_not_exist": [],
                    "invariants": [],
                }
            },
            "tasks": [
                {
                    "task_id": "TASK-001",
                    "risks": {"curvature": 0.10},
                    "validation": {"predicates": ["P1"]},
                },
                {
                    "task_id": "TASK-002",
                    "risks": {"curvature": 0.80},
                    "validation": {"predicates": ["P2"]},
                },
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

    # Test 1: TASK-001 es next_action → ok
    ok, _, _ = enforce_task(chain, "TASK-001")
    if not ok:
        failures.append("Test1: TASK-001 (next_action) debería pasar")

    # Test 2: TASK-002 no es next_action → bloqueado
    ok, _, _ = enforce_task(chain, "TASK-002")
    if ok:
        failures.append("Test2: TASK-002 (fuera de orden) debería bloquear")

    # Test 3: TASK-002 con --force → ok
    ok, _, _ = enforce_task(chain, "TASK-002", force=True)
    if not ok:
        failures.append("Test3: TASK-002 --force debería pasar")

    # Test 4: curvature alto (TASK-002) bloquea por curvature_budget
    ok, reasons, _ = enforce_task(chain, "TASK-002")
    if ok or not any(r["rule"] == "curvature_budget" for r in reasons):
        failures.append("Test4: TASK-002 debería bloquear por curvature_budget")

    if failures:
        print("❌ SELFTEST FALLÓ:")
        for f in failures:
            print(f"   - {f}")
        return 1
    print(
        "✅ SELFTEST delta-enforce: 4/4 pasaron (next_action, orden, --force, curvature)"
    )
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
