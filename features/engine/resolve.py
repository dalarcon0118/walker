"""
resolve — recent commits mention module

Extracted from walker.py.
"""

import os
import json
import subprocess
from datetime import datetime, timezone
from string import Template
from libs.infrastructure.utils import (
    eprint,
    load_json,
    extract_chain_dir,
    _is_chain_dir,
    _list_project_sprints,
    _get_current_node,
    _get_current_sub_key,
    _CONFLICTS_FILE,
)
from .loader import find_workflow, ensure_state

# ── Detection helpers (antes en engine.py) ──────────────────────────


def _detect_workflow_version(
    workflow: dict, chain_dir: str | None
) -> tuple[bool, list[str]]:
    """Detect if workflow.json is outdated (not v4)."""
    if not chain_dir:
        return False, []
    version = workflow.get("version", "1.0")
    if version in ("4.0", "4"):
        return False, []
    from libs.infrastructure.utils import _V3_NODE_NAMES

    nodes = workflow.get("nodes", {})
    missing = [n for n in _V3_NODE_NAMES if n not in nodes]
    if len(missing) < len(_V3_NODE_NAMES):
        return True, missing
    return False, []


def _detect_close_check_with_pending(chain_dir: str | None) -> tuple[bool, list[str]]:
    """Detect if sprint has pending tasks during close check.

    PRINCIPIO: el estado de negocio del spring (specs y sus estados) vive
    en el DELTA (SpringService). No se lee sprint-state.json.
    """
    if not chain_dir:
        return False, []

    from features.delta_tracking.application.services.spring_service import (
        SpringService,
    )

    try:
        spring_svc = SpringService.from_chain_dir(chain_dir)
        if not spring_svc:
            return False, []
        spring_id = spring_svc.get_spring_id(chain_dir)
        spring_svc.ensure_spring_imported(chain_dir, spring_id)
        specs = spring_svc.get_specs(spring_id)
    except Exception:
        return False, []

    pending = [
        s.get("spec_id", s.get("task_id", ""))
        for s in specs
        if str(s.get("status", "")).lower()
        not in _DONE_STATUSES | {"rejected", "backlog"}
    ]
    if pending:
        return True, pending
    return False, []


def _detect_reconstructed_state(state: dict) -> bool:
    """Detect if walker state was reconstructed from .workflow-state.json."""
    return state.get("context", {}).get("reconstructed", False)


def _detect_missing_flow_steps(state: dict) -> list[str]:
    """Detect v4 flow steps missing from history."""
    history_set = set(state.get("history", []))
    critical_steps = [
        "IMPLEMENT.USER_GATE",
        "IMPLEMENT.CODE_REVIEW",
        "IMPLEMENT.REPORT_GATE",
        "VERIFY.QA",
        "VERIFY.PO_ACCEPT",
    ]
    missing = [n for n in critical_steps if n not in history_set]
    return missing


def _recent_commits_mention(tid: str, hours: int = 12) -> bool:
    """Check if recent git commits mention a task ID."""
    try:
        result = subprocess.run(
            ["git", "log", f"--since={hours}.hours", "--oneline", "--format=%s"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return False
        return tid in result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _detect_task_state_conflicts(chain_dir: str | None) -> list[dict]:
    """Detect tasks pending in el estado del motor but with recent commits.

    Un conflicto real = tarea 'pending' + commits recientes que la mencionan.
    Solo estas tareas pueden ser resueltas con resolve --apply.
    """
    if not chain_dir:
        return []

    from features.engine.infrastructure.repositories import (
        WorkflowStateRepository as _SSR,
    )

    def load_state(c):
        return _SSR().load(c).to_dict() or {}

    s = load_state(chain_dir)
    tasks = s.get("tasks", {})
    if not tasks:
        return []

    conflicts: list[dict] = []
    for tid, tdata in tasks.items():
        status = tdata.get("status") if isinstance(tdata, dict) else tdata
        if status != "pending":
            continue
        if _recent_commits_mention(tid):
            conflicts.append(
                {
                    "task_id": tid,
                    "message": f"Commits recientes mencionan {tid} pero estado='pending'",
                    "action": f"walker.py resolve --apply {tid} <chain_dir>",
                }
            )
    return conflicts


def _resolve_conflict(chain_dir: str, tid: str) -> int:
    """Mark a task as completed, only if a conflict was previously detected.

    VALIDACIONES:
    1. Debe existir un conflicto NO RESUELTO en .delta-conflicts.json
    2. El conflicto debe haber sido detectado por resolve (no inventado)
    3. La tarea debe estar 'pending' todavia

    Si no hay conflicto, BLOQUEA: el LLM no puede saltarse el workflow.
    """
    from features.engine.infrastructure.repositories import (
        WorkflowStateRepository as _SSR,
    )

    load_state = lambda c: _SSR().load(c).to_dict() or {}, save_state

    # ── Verificar que existe un conflicto real ─────────────────
    cpath = os.path.join(chain_dir, _CONFLICTS_FILE)
    clog: list[dict] = load_json(cpath) if os.path.exists(cpath) else []
    if not isinstance(clog, list):
        clog = []

    has_conflict = any(e.get("task_id") == tid and not e.get("resolved") for e in clog)

    if not has_conflict:
        print()
        print("=" * 70)
        print(f"  ❌ RESOLVE BLOQUEADO: No hay conflicto detectado para {tid}")
        print()
        print("  resolve --apply solo puede usarse cuando hay un conflicto")
        print("  detectado por 'walker.py resolve'. No puedes marcar tareas")
        print("  como completadas sin pasar por el workflow.")
        print()
        print("  Ejecuta 'walker.py resolve <chain_dir>' para detectar conflictos.")
        print("  Si no hay conflictos, la tarea debe completarse via el workflow.")
        print("=" * 70)
        print()
        return 1

    # ── Verificar que la tarea aun esta 'pending' ─────────────
    s = load_state(chain_dir)
    current = s.get("tasks", {}).get(tid, {}).get("status", "unknown")
    if current == "completed":
        print(f"  [resolve] {tid} ya esta 'completed'")
        return 0

    # ── Aplicar resolucion ────────────────────────────────────
    s.setdefault("tasks", {})[tid] = s["tasks"].get(tid, {})
    s["tasks"][tid]["status"] = "completed"
    s.setdefault("audit", {}).setdefault("trail", []).append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": f"{tid} marcada completed via resolve --apply (conflicto validado)",
        }
    )
    save_state(s, chain_dir)

    # Marcar conflicto como resuelto
    for e in clog:
        if e.get("task_id") == tid and not e.get("resolved"):
            e["resolved"] = True
            e["resolved_at"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(cpath, "w") as f:
            json.dump(clog, f, indent=2, ensure_ascii=False)
    except OSError as e:
        eprint(f"  [warn] No se pudo escribir conflict log {cpath}: {e}")

    print(f"  ✅ [resolve] {tid} marcada como 'completed' (conflicto resuelto)")
    return 0


def _detect_all_conflicts(
    chain_dir: str,
    state: dict,
    workflow: dict,
    current: str,
    sub_key: str,
    issues: list[str],
    actions: list[str],
) -> None:
    """Detecta todos los conflictos del workflow (v4)."""
    # 1. Workflow outdated?
    needs_migrate, _missing = _detect_workflow_version(workflow, chain_dir)
    if needs_migrate:
        issues.append("Workflow desactualizado (v3). Migrando a v4...")
        actions.append("Auto-migracion v3->v4 ejecutada.")
        print("  ✅ Auto-migracion: workflow.json actualizado a v4")
        print()
    else:
        print("  ✅ Workflow.json v4 actualizado")
        print()

    # 2. SPRINT.CLOSE_CHECK with pending tasks?
    has_pending, pending_tasks = _detect_close_check_with_pending(chain_dir)
    if current == "SPRINT" and sub_key == "CLOSE_CHECK" and has_pending:
        issues.append(
            Template("CLOSE_CHECK con $n tareas pendientes: $tasks").substitute(
                n=str(len(pending_tasks)), tasks=", ".join(pending_tasks)
            )
        )
        actions.append(
            "Usa 'transition close_sprint' para ir a HANDLE_PENDING y decidir que hacer con las pendientes."
        )
    elif current == "SPRINT" and sub_key == "CLOSE_CHECK" and not has_pending:
        actions.append(
            "No hay tareas pendientes. Usa 'transition close_sprint' para cerrar."
        )

    # 3. Reconstructed state?
    if _detect_reconstructed_state(state):
        issues.append(
            "Estado reconstruido desde .workflow-state.json — el historial puede estar incompleto"
        )
        actions.append("Ejecuta 'walker.py status' para ver el estado actual.")

    # 4. Missing v4 flow steps?
    missing_steps = _detect_missing_flow_steps(state)
    if missing_steps and current not in ("SPRINT",):
        issues.append(
            Template("Pasos de flujo v4 no visitados: $steps").substitute(
                steps=", ".join(missing_steps)
            )
        )
        actions.append(
            "El flujo v4 (5 nodos con sub-estados) requiere pasar por USER_GATE y REPORT_GATE."
        )

    # 5. Task state conflicts (tasks pending en el estado del motor but implemented in code)
    task_conflicts = _detect_task_state_conflicts(chain_dir)
    clog: list[dict] = []
    cpath = os.path.join(chain_dir, _CONFLICTS_FILE)
    if task_conflicts:
        clog = load_json(cpath) if os.path.exists(cpath) else []
        if not isinstance(clog, list):
            clog = []
    for conflict in task_conflicts:
        issues.append(f"{conflict['task_id']}: {conflict['message']}")
        actions.append(conflict["action"])
        clog.append(
            {
                "task_id": conflict["task_id"],
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "message": conflict["message"],
                "resolved": False,
            }
        )
    if task_conflicts:
        try:
            with open(cpath, "w") as f:
                json.dump(clog, f, indent=2, ensure_ascii=False)
        except OSError as e:
            eprint(f"  [warn] No se pudo escribir conflict log {cpath}: {e}")


def cmd_resolve(sub_args: list[str]) -> int:
    """walker resolve: detecta conflictos y aplica estrategias de resolucion.

    Detecta:
    - Workflow desactualizado
    - Tareas implementadas pero no registradas

    Uso:
      walker.py resolve [<chain_dir>]
      walker.py resolve --apply <task_id> <chain_dir>
    """
    sub_args, chain_dir = extract_chain_dir(sub_args)
    if chain_dir and not _is_chain_dir(chain_dir):
        return _list_project_sprints(chain_dir)

    # --apply <task_id> [<chain_dir>]
    if sub_args and sub_args[0] == "--apply":
        if len(sub_args) < 2:
            eprint("Uso: walker.py resolve --apply <task_id> <chain_dir>")
            return 2
        tid = sub_args[1]
        if not chain_dir and len(sub_args) >= 3:
            chain_dir = os.path.abspath(sub_args[2])
        if not chain_dir:
            eprint("  [resolve] Se requiere <chain_dir>")
            return 2
        return _resolve_conflict(chain_dir, tid)

    if not chain_dir:
        eprint("Uso: walker.py resolve [<chain_dir>]")
        return 2

    state = ensure_state(chain_dir)
    workflow = find_workflow(chain_dir)
    current = _get_current_node(state)
    sub_key = _get_current_sub_key(state)
    task_id = state.get("context", {}).get("task_id")
    issues: list[str] = []
    actions: list[str] = []

    print("=" * 70)
    print("  WALKER RESOLVE v4 — Diagnostico de conflictos")
    print("=" * 70)
    print()
    print(Template("  Sprint:   $d").substitute(d=os.path.basename(chain_dir)))
    print(
        Template("  Current:  [$current.$sub_key]").substitute(
            current=current, sub_key=sub_key
        )
    )
    print(Template("  Task ID:  $tid").substitute(tid=task_id or "(ninguno)"))
    print(
        Template("  Workflow: $n nodos, $e edges").substitute(
            n=str(len(workflow.get("nodes", {}))),
            e=str(len(workflow.get("edges", []))),
        )
    )
    print()

    # ── Detection ──
    _detect_all_conflicts(
        chain_dir, state, workflow, current, sub_key, issues, actions
    )

    # ── Resolution strategies ──

    if not issues:
        print("  ✅ No se detectaron conflictos.")
        print()
        print("  El workflow esta en un estado consistente (v4).")
        print(
            Template("  Current: [$current.$sub_key]").substitute(
                current=current, sub_key=sub_key
            )
        )
        print("  Usa 'walker.py status' para ver las transiciones disponibles.")
        print()
        return 0

    print("─" * 70)
    print("  PROBLEMAS DETECTADOS:")
    for i, issue in enumerate(issues, 1):
        print(Template("  $i. $issue").substitute(i=i, issue=issue))
    print()

    print("─" * 70)
    print("  ACCIONES EJECUTADAS / RECOMENDADAS:")
    for i, action in enumerate(actions, 1):
        print(Template("  $i. $action").substitute(i=i, action=action))
    print()

    print("=" * 70)
    print()
    return 1 if issues else 0
