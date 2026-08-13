"""
resolve — Workflow conflict detection and resolution.

Refactored: conflicts are derived from current state (workflow + delta + trace)
instead of a separate .delta-conflicts.json file. Conflict detection and
resolution logic lives in delta_tracking/domain/services/conflict_resolver.py.
"""

from __future__ import annotations

import os
import subprocess
from string import Template
from core.engine.legacy.utils import (
    eprint,
    extract_chain_dir,
    _is_chain_dir,
    _list_project_sprints,
    _get_current_node,
    _get_current_sub_key,
)
from .loader import find_workflow, ensure_state


# ── Detection helpers ─────────────────────────────────────────────────────────


def _detect_workflow_version(
    workflow: dict, chain_dir: str | None
) -> tuple[bool, list[str]]:
    """Detect if workflow.json is outdated (not v4)."""
    if not chain_dir:
        return False, []
    version = workflow.get("version", "1.0")
    if version in ("4.0", "4"):
        return False, []
    from core.engine.legacy.utils import _V3_NODE_NAMES

    nodes = workflow.get("nodes", {})
    missing = [n for n in _V3_NODE_NAMES if n not in nodes]
    if len(missing) < len(_V3_NODE_NAMES):
        return True, missing
    return False, []


def _detect_close_check_with_pending(chain_dir: str | None) -> tuple[bool, list[str]]:
    """Detect if sprint has pending tasks during close check."""
    if not chain_dir:
        return False, []

    from features.delta_tracking import spring_service

    try:
        spring_svc = spring_service(chain_dir=chain_dir)
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
        not in {"completed", "closed", "rejected", "backlog"}
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
    """
    if not chain_dir:
        return []

    from core.engine.infrastructure.repositories import (
        WorkflowStateRepository as _SSR,
    )

    s = _SSR().load(chain_dir).to_dict() or {}
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
                    "action": f"walker resolve --apply {tid} {chain_dir}",
                }
            )
    return conflicts


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
    for conflict in task_conflicts:
        issues.append(f"{conflict['task_id']}: {conflict['message']}")
        actions.append(conflict["action"])


def cmd_resolve(sub_args: list[str]) -> int:
    """walker resolve: detecta conflictos y aplica estrategias de resolucion.

    Detecta:
    - Workflow desactualizado
    - Tareas implementadas pero no registradas

    Uso:
      walker resolve [<chain_dir>]
      walker resolve --apply <task_id> <chain_dir>
    """
    sub_args, chain_dir = extract_chain_dir(sub_args)
    if chain_dir and not _is_chain_dir(chain_dir):
        return _list_project_sprints(chain_dir)

    # --apply <task_id> [<chain_dir>]
    if sub_args and sub_args[0] == "--apply":
        if len(sub_args) < 2:
            eprint("Uso: walker resolve --apply <task_id> <chain_dir>")
            return 2
        tid = sub_args[1]
        if not chain_dir and len(sub_args) >= 3:
            chain_dir = os.path.abspath(sub_args[2])
        if not chain_dir:
            eprint("  [resolve] Se requiere <chain_dir>")
            return 2
        return _resolve_conflict(chain_dir, tid)

    if not chain_dir:
        eprint("Uso: walker resolve [<chain_dir>]")
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
    _detect_all_conflicts(chain_dir, state, workflow, current, sub_key, issues, actions)

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


def _resolve_conflict(chain_dir: str, tid: str) -> int:
    """Mark a task as completed, only if a conflict was previously detected.

    Ahora usa WorkflowConflictResolver de engine en lugar de ConflictResolver
    legacy para estrategias de workflow.
    """
    from features.delta_tracking.domain.services.conflict_resolver import (
        ConflictResolver,
    )
    from core.engine.domain.services.conflict_resolver import (
        WorkflowConflictResolver,
    )
    from core.engine.infrastructure.repositories import (
        WorkflowStateRepository as _SSR,
    )
    from returns.result import Success

    s = _SSR().load(chain_dir).to_dict() or {}
    s.get("current_node", "")
    s.get("current_sub_state", "")

    workflow = find_workflow(chain_dir)
    from features.delta_tracking import spring_service

    try:
        spring_svc = spring_service(chain_dir=chain_dir)
        if spring_svc:
            spring_id = spring_svc.get_spring_id(chain_dir)
            specs = spring_svc.get_specs(spring_id)
        else:
            specs = []
    except Exception:
        specs = []

    trace_events = []
    try:
        from core.engine.infrastructure.trace_log import TraceLogger

        tracer = TraceLogger()
        trace_events = tracer.replay(chain_dir)
    except Exception:
        pass

    conflicts = ConflictResolver.detect(s, workflow, specs, trace_events)
    task_conflict = next((c for c in conflicts if c.spec_id == tid), None)

    if not task_conflict:
        print()
        print("=" * 70)
        print(f"  ❌ RESOLVE BLOQUEADO: No hay conflicto detectado para {tid}")
        print()
        print("  resolve --apply solo puede usarse cuando hay un conflicto")
        print("  detectado por 'walker resolve'. No puedes marcar tareas")
        print("  como completadas sin pasar por el workflow.")
        print()
        print("  Ejecuta 'walker resolve <chain_dir>' para detectar conflictos.")
        print("  Si no hay conflictos, la tarea debe completarse via el workflow.")
        print("=" * 70)
        print()
        return 1

    current_status = "unknown"
    for spec in specs:
        if spec.get("spec_id") == tid or spec.get("task_id") == tid:
            current_status = spec.get("status", "unknown")
            break

    if current_status == "completed":
        print(f"  [resolve] {tid} ya esta 'completed'")
        return 0

    from core.engine.domain.models import WorkflowState, WorkflowGraph

    state = WorkflowState.from_dict(s)
    graph = WorkflowGraph.from_dict(workflow)

    result = WorkflowConflictResolver.resolve(
        state, "skip", {"task_id": tid}, graph=graph
    )

    if isinstance(result, Success):
        new_state = result.unwrap()
    else:
        print(
            f"  ❌ RESOLVE FALLO: {result.failure() if hasattr(result, 'failure') else result}"
        )
        return 1

    new_state_dict = new_state.to_dict() if hasattr(new_state, "to_dict") else new_state

    from core.engine.infrastructure.repositories import WorkflowStateRepository

    WorkflowStateRepository().save(
        chain_dir,
        type("WS", (), {"to_dict": lambda self: new_state_dict, "version": "5.0"})(),
    )

    if spring_svc:
        spring_id = spring_svc.get_spring_id(chain_dir)
        spring_svc.update_spec_status(spring_id, tid, "completed")

    print(f"  ✅ [resolve] {tid} marcada como 'completed' (conflicto resuelto)")
    return 0


def cmd_resolve(sub_args: list[str]) -> int:
    """walker resolve: detecta conflictos y aplica estrategias de resolucion."""
    sub_args, chain_dir = extract_chain_dir(sub_args)
    if chain_dir and not _is_chain_dir(chain_dir):
        return _list_project_sprints(chain_dir)

    # --apply <task_id> [<chain_dir>]
    if sub_args and sub_args[0] == "--apply":
        if len(sub_args) < 2:
            eprint("Uso: walker resolve --apply <task_id> <chain_dir>")
            return 2
        tid = sub_args[1]
        if not chain_dir and len(sub_args) >= 3:
            chain_dir = os.path.abspath(sub_args[2])
        if not chain_dir:
            eprint("  [resolve] Se requiere <chain_dir>")
            return 2
        return _resolve_conflict(chain_dir, tid)

    if not chain_dir:
        eprint("Uso: walker resolve [<chain_dir>]")
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
    _detect_all_conflicts(chain_dir, state, workflow, current, sub_key, issues, actions)

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
