"""utils.py — Shared utilities for the delta skill walker engine.

PROXY MODULE: Re-exports from _walker/shared/utils.py for backward compat.
New code should import directly from _walker.shared.utils.
"""

from __future__ import annotations

import glob
import json
import os
from string import Template

# ── Re-export from _walker/shared/utils.py ──────────────────────────────
from _walker.shared.utils import (
    eprint,
    _valid_transitions,
)

# Backward-compat alias: external modules import valid_transitions (no underscore)
valid_transitions = _valid_transitions

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_SCRIPT_DIR)  # .claude/skills/delta/
_PROJECTS_DIR = ".cognitive/projects"
_DELTA_DIR = "delta"
_SCRIPTS_DIR = os.path.join(_BASE_DIR, "scripts")
_SPRING_SCRIPT = os.path.join(_SCRIPTS_DIR, "spring.py")
_DELTA_GATE_SCRIPT = os.path.join(_SCRIPTS_DIR, "delta-gate-check.py")
_TASK_SCRIPT = os.path.join(_SCRIPTS_DIR, "task.py")
_INIT_SCRIPT = ""  # DEPRECATED: delta-init-project.py eliminado (TASK-011)
_DONE_STATUSES = {"completed", "achieved", "cancelled", "closed", "done"}


def canonical_workflow_path(project_id: str | None = None) -> str:
    """Deriva la ruta al workflow canónico desde project_id o detectándolo del cwd."""
    from _walker.shared.path_resolver import get_project_dir

    if not project_id:
        from _walker.shared.path_resolver import resolve_project_id

        project_id = resolve_project_id(os.getcwd()) or "default"
    return str(get_project_dir(project_id) / "workflow.json")


# Legacy alias — usa canonical_workflow_path() en lugar de esta constante
_CANONICAL_WORKFLOW = canonical_workflow_path("delta-skill")
_V4_NODES = ["SPRINT", "ANALYZE", "IMPLEMENT", "VERIFY", "REFINE"]
_V4_EDGES = [
    ("SPRINT", "ANALYZE", "start_task"),
    ("SPRINT", "SPRINT", "edit_plan"),
    ("SPRINT", "SPRINT", "close_sprint"),
    ("SPRINT", "SPRINT", "back_to_active"),
    ("ANALYZE", "IMPLEMENT", "analysis_approved"),
    ("ANALYZE", "REFINE", "needs_refine"),
    ("IMPLEMENT", "VERIFY", "implementation_done"),
    ("IMPLEMENT", "REFINE", "needs_refine"),
    ("IMPLEMENT", "SPRINT", "cancel"),
    ("VERIFY", "SPRINT", "task_complete"),
    ("VERIFY", "REFINE", "needs_refine"),
    ("REFINE", "ANALYZE", "reroute_analyze"),
    ("REFINE", "IMPLEMENT", "reroute_implement"),
    ("REFINE", "VERIFY", "reroute_verify"),
    ("REFINE", "SPRINT", "reroute_sprint"),
]
_V3_NODE_NAMES = [
    "INIT",
    "CHECK_SPRINTS",
    "CREATE_SPRINT",
    "SPRINT_ACTIVE",
    "TASK_PENDING",
    "IN_PROGRESS",
    "PO_REVIEW",
    "RESEARCH",
    "ANALYSIS_GATE",
    "ANALYSIS_REVIEW",
    "USER_GATE",
    "ADVANCE_TASK",
    "DONE_REVIEW",
    "CODE_REVIEW",
    "REPORT_GATE",
    "VERIFY",
    "PO_ACCEPTANCE",
    "REFINE",
    "EDIT_PLAN",
    "CLOSE_CHECK",
    "CLOSE_SPRINT",
    "HANDLE_PENDING",
]


def _resolve_chain_id(chain_id: str) -> str | None:
    """Resolve a chain_id (e.g. 'workflow-engine-v2-20260723') to its full path.

    Scans .cognitive/projects/*/delta/*/ for matching chain directories.
    Supports 'project_id:chain_id' format for disambiguation.
    Returns the full path or None if not found.
    """
    from _walker.shared.path_resolver import get_projects_dir

    # Support project_id:chain_id syntax for disambiguation
    project_filter = None
    search_id = chain_id
    if ":" in chain_id:
        parts = chain_id.split(":", 1)
        project_filter = parts[0]
        search_id = parts[1]

    projects_dir = str(get_projects_dir())
    if not os.path.isdir(projects_dir):
        return None

    matches = []
    pattern = os.path.join(projects_dir, "*", "delta", "*", "sprint-state.json")
    for sprint_state_path in glob.glob(pattern):
        chain_dir = os.path.dirname(sprint_state_path)
        parts = chain_dir.split(os.sep)
        try:
            proj_idx = parts.index("projects")
            project_id = parts[proj_idx + 1]
            cid = parts[-1]
        except (ValueError, IndexError):
            continue

        if cid != search_id:
            continue
        if project_filter and project_id != project_filter:
            continue

        matches.append((project_id, cid, chain_dir))

    if len(matches) == 0:
        return None

    if len(matches) > 1:
        eprint(
            f"⚠️  Multiple projects have chain_id '{search_id}': "
            + ", ".join(m[0] for m in matches)
        )
        eprint(f"   Use 'project_id:{search_id}' to disambiguate.")
        eprint(f"   Using first match: {matches[0][0]}")
        eprint()

    return matches[0][2]


def extract_chain_dir(args: list[str]) -> tuple[list[str], str | None]:
    """Extract optional chain_dir from first positional.

    Resolution order:
      1. If it contains '/' or exists as a directory → treat as path
      2. Otherwise → try to resolve as chain_id scanning all projects
         (supports 'project_id:chain_id' for disambiguation)
    """
    if args and not args[0].startswith("--"):
        candidate = args[0]
        if "/" in candidate or os.path.isdir(candidate):
            return args[1:], os.path.abspath(candidate)
        # Try to resolve as chain_id
        resolved = _resolve_chain_id(candidate)
        if resolved:
            return args[1:], resolved
    return args, None


def _resolve_task_id(
    chain_dir: str | None, skip_tasks: set[str] | None = None
) -> str | None:
    """Read the first pending task_id from sprint-state.json, skipping done/blocked tasks.

    Salta:
    - Tareas en _DONE_STATUSES (completed, etc.)
    - Tareas en _BLOCKED_STATUSES (rejected, needs_revision, blocked)
    - Tareas en skip_tasks (rejected_tasks del walker state)
    - Tareas con po_rejected en decision_log (fallback si skip_tasks no está disponible)
    """
    if not chain_dir:
        return None
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")
    if not os.path.exists(sprint_state_path):
        return None
    try:
        with open(sprint_state_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return None

    skip = skip_tasks or set()
    blocked = _DONE_STATUSES | {"rejected", "needs_revision", "blocked", "backlog"}

    # Add tasks that were po_rejected in decision_log
    log = data.get("progress", data).get("walker_data", {}).get("decision_log", [])
    for entry in log:
        if entry.get("gate") == "po_rejected" and entry.get("task_id"):
            skip.add(entry["task_id"])

    prog = data.get("progress", data)
    task_statuses = prog.get("tasks", {})
    # Normalize list → dict
    if isinstance(task_statuses, list):
        task_statuses = {tid: "pending" for tid in task_statuses}
    for task_id, status in task_statuses.items():
        if task_id in skip:
            continue
        # Support both string and dict formats
        if isinstance(status, str):
            status_str = status.lower()
        elif isinstance(status, dict):
            status_str = status.get("status", "").lower()
        else:
            continue
        if status_str not in blocked:
            return task_id
    return None


def _list_project_sprints(project_path: str) -> int:
    """Show available sprints in a project directory. Returns 1 (not a chain_dir)."""
    from _walker.shared.path_resolver import get_delta_dir

    # Intenta derivar project_id del path o usarlo como delta dir directamente
    delta_dir = str(get_delta_dir(os.path.basename(project_path)))
    if not os.path.isdir(delta_dir):
        # Maybe the path IS a delta dir
        delta_dir = project_path

    sprints = []
    if os.path.isdir(delta_dir):
        for entry in sorted(os.listdir(delta_dir)):
            prog = os.path.join(delta_dir, entry, "sprint-state.json")
            if os.path.isfile(prog):
                import json as _json

                try:
                    with open(prog) as f:
                        data = _json.load(f)
                    prog_data = data.get("progress", data)
                    status = prog_data.get("meta", {}).get("status", "unknown")
                    tasks = len(prog_data.get("task_states", {}))
                except (json.JSONDecodeError, KeyError, OSError):
                    status = "error"
                    tasks = 0
                sprints.append((entry, status, tasks, os.path.join(delta_dir, entry)))

    print()
    print("=" * 70)
    print("  ESTE NO ES UN DIRECTORIO DE SPRINT VALIDO")
    print("=" * 70)
    print()
    print(Template("  Path: $p").substitute(p=project_path))
    print("  Un chain_dir debe ser un directorio de sprint con sprint-state.json,")
    print("  ej: .../delta/skill-spring-delta")
    print()

    if sprints:
        print(
            Template("  Sprints disponibles en este proyecto ($n):").substitute(
                n=len(sprints)
            )
        )
        print()
        print("  Chain ID                                        Estado      Tareas")
        print("  " + "-" * 65)
        for cid, st, tcount, cpath in sprints:
            print(
                Template("  $cid  $st  $count").substitute(
                    cid=cid.ljust(43), st=st.ljust(10), count=str(tcount)
                )
            )
        print()
        print("  Usa: walker.py status <chain_dir>")
        print(Template("  ej: walker.py status $ex").substitute(ex=sprints[0][3]))
    else:
        print("  No se encontraron sprints en este proyecto.")
        print("  Usa: walker.py transition null  (desde INIT) para crear uno.")
    print("=" * 70)
    print()
    return 1


def _get_task_schema_requirements() -> tuple[set[str], dict]:
    """Load plan.schema.json and extract required fields + defaults for tasks."""
    schema_path = os.path.join(_BASE_DIR, "schema", "plan.schema.json")
    required = {"task_id", "objective", "result_state", "type", "domain"}
    defaults = {}
    if os.path.exists(schema_path):
        try:
            with open(schema_path) as f:
                schema = json.load(f)
            task_def = schema.get("$defs", {}).get("task", {})
            required = set(task_def.get("required", required))
            # Defaults for optional fields with obvious values
            defaults = {
                "importer_files": 0,
                "depends_on": [],
                "preconditions": [],
                "acceptance_criteria": [],
                "quality_gates": [],
                "loop_verification": [],
                "loop_acceptance": "all",
                "decisions": [],
                "refinement": {},
                "task_description": "",
                "validation": {"predicates": []},
                "impacted_artifacts": {"files": []},
                "risks": {"curvature": 0.2, "pre_churn": 0.1, "commit_trust": 0.9},
                "task_context": {
                    "observation": "",
                    "structure": "",
                    "behavior": "",
                    "causality": "",
                    "purpose": "",
                    "principles": "",
                },
            }
        except (json.JSONDecodeError, OSError) as e:
            eprint(f"  [error] Error leyendo schema requirements: {e}")
    return required, defaults


_TASK_SCHEMA_REQUIRED, _TASK_DEFAULTS = _get_task_schema_requirements()
_EDITABLE_FIELDS = {
    "objective",
    "result_state",
    "domain",
    "work_type",
    "type",
    "role",
    "scrum_role",
    "risks",
    "impacted_artifacts",
    "validation",
    "depends_on",
    "acceptance_criteria",
    "quality_gates",
    "task_context",
    "status",
    "preconditions",
    "decisions",
    "refinement",
    "task_description",
    "importer_files",
    "loop_verification",
    "loop_acceptance",
}


def _backlog_path(chain_dir: str) -> str:
    """Path to project-level backlog.json."""
    from _walker.shared.path_resolver import resolve_project_root

    return os.path.join(resolve_project_root(chain_dir), "backlog.json")


def _project_dir(chain_dir: str) -> str:
    """Derive project directory from a chain_dir."""
    from _walker.shared.path_resolver import resolve_project_root

    return resolve_project_root(chain_dir)


def _find_cognitive_root() -> str | None:
    """Find the .cognitive directory by searching upward from cwd."""
    cwd = os.getcwd()
    for _ in range(15):
        if os.path.isdir(os.path.join(cwd, ".cognitive")):
            return os.path.join(cwd, ".cognitive")
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent
    return None


def _find_active_sprints() -> list[dict]:
    """Scan all projects for active sprints with walker state."""
    from _walker.shared.path_resolver import get_projects_dir

    projects_dir = str(get_projects_dir())
    if not os.path.isdir(projects_dir):
        return []

    results = []
    seen_chains = set()

    for sprint_state_path in glob.glob(
        os.path.join(projects_dir, "*", "delta", "*", "sprint-state.json")
    ):
        chain_dir = os.path.dirname(sprint_state_path)
        parts = chain_dir.split(os.sep)
        # parts = [..., "projects", "<pid>", "delta", "<cid>"]
        try:
            proj_idx = parts.index("projects")
            project_id = parts[proj_idx + 1]
            chain_id = parts[-1]
        except (ValueError, IndexError):
            project_id = "?"
            chain_id = os.path.basename(chain_dir)

        if chain_id in seen_chains:
            continue
        seen_chains.add(chain_id)

        try:
            with open(sprint_state_path) as f:
                data = json.load(f)
            prog = data.get("progress", data)
            status = prog.get("meta", {}).get(
                "status", data.get("sprint", {}).get("status", "unknown")
            )
            tasks = len(prog.get("task_states", {}))
        except (json.JSONDecodeError, KeyError, OSError):
            status = "error"
            tasks = 0

        # Filter: solo incluir sprints con estado activo
        if status != "active":
            continue

        # Walk state (v4: current_node.current_sub_state desde sprint-state.json)
        walker_state = None
        state_file = os.path.join(chain_dir, "sprint-state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file) as f:
                    ss = json.load(f)
                w = ss.get("walker", {})
                node = w.get("current_node", "?")
                sub = w.get("current_sub_state", "")
                walker_state = f"{node}.{sub}" if sub else node
            except (json.JSONDecodeError, OSError) as e:
                eprint(f"  [warn] Error leyendo walker state {state_file}: {e}")

        results.append(
            {
                "project_id": project_id,
                "chain_id": chain_id,
                "chain_dir": chain_dir,
                "status": status,
                "tasks": tasks,
                "walker_state": walker_state,
            }
        )

    results.sort(key=lambda r: (r["project_id"], r["chain_id"]))
    return results


_CONFLICTS_FILE = ".delta-conflicts.json"
_HELP = Template("""walker.py v4 — Workflow graph engine del skill delta

USO (agente — comandos principales):
  walker.py submit [<chain_dir>] [--task_id X] [--key value ...]  → llena datos, sistema avanza
  walker.py status [<chain_dir> | --project <id>] [--deep]        → dashboard completo
  walker.py current [<chain_dir> | --project <id>]                → sub-estado actual

  El agente SOLO usa submit para navegar. submit valida required_fields,
  auto-avanza sub-estados mode:auto, y auto-transiciona entre nodos
  cuando hay una sola salida valida.

USO (admin — comandos auxiliares):
  walker.py resume <node> [<chain_dir>]        → salto validado (verifica modelo)
  walker.py transition <gate> [<chain_dir>]     → transicion manual entre nodos
  walker.py resolve [<chain_dir>]               → resolver conflictos
  walker.py goto <node> [<chain_dir>]           → emergencia: saltar validaciones
  walker.py reset [<chain_dir>]                 → reiniciar a SPRINT.INIT
  walker.py health [<chain_dir>]                → diagnosticar estado
  walker.py verify <chain_dir> <task_id>        → verificar tarea
  walker.py approve-task <chain_dir> <task_id>  → aprobar tarea
  walker.py backlog list|move <chain_dir>       → gestionar backlog
  walker.py query task|file|... <arg> [<chain_dir>]

  V4: 5 nodos con sub-estados deterministas (ELM/Saga).
  submit → LLM llena modelo del sub-estado actual, sistema navega.
  transition → solo admin, para depuración o recovery manual.

  spring.py/task.py/delta-gate-check.py son llamados automaticamente
  por el engine via auto_complete en sub-estados.

  --task_id es OPCIONAL: se resuelve desde sprint-state.json o context.""")


def print_help():
    print(_HELP.substitute())
