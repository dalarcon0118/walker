"""utils.py — Shared utilities for the delta skill walker engine.

SSOT: Este módulo implementa TODAS sus utilidades de forma autónoma.
PRINCIPIO: No depende de código legacy (_walker, to_clean_next) ni
inyecta directorios externos en sys.path. Si una función no puede
hacer su trabajo, ERROR — nunca fallback silencioso.
"""
from __future__ import annotations

import glob
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from string import Template


_SCRIPT_TIMEOUT = 120


# ── Format helpers ──────────────────────────────────────────────────────────


def eprint(*args, **kwargs):
    """Print to stderr."""
    print(*args, file=sys.stderr, **kwargs)


def _report_invalid_gate(workflow: dict, node_id: str, gate: str) -> int:
    """Print error for invalid gate and return exit code 1."""
    trans = _valid_transitions(workflow, node_id)
    valid_gates = [t["gate"] for t in trans if t.get("gate")] + [
        "(sin gate)" for t in trans if not t.get("gate")
    ]
    eprint(
        Template("Gate invalido desde [$node_id]: $gate").substitute(
            node_id=node_id, gate=gate
        )
    )
    eprint(Template("  Gates validos: $gates").substitute(gates=", ".join(valid_gates)))
    return 1


# ── File operations ─────────────────────────────────────────────────────────


def load_json(path: str) -> dict | None:
    """Load and parse a JSON file. Returns None on parse failure (no raise)."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def state_path(chain_dir: str | None) -> str | None:
    """Build path to sprint-state.json within a chain directory."""
    if chain_dir:
        return os.path.join(chain_dir, "sprint-state.json")
    return None


def save_state(state: dict, chain_dir: str | None = None) -> None:
    """Persist walker state to chain_dir. No-op without chain_dir."""
    if not chain_dir:
        return
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    sp = state_path(chain_dir)
    if sp:
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        with open(sp, "w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)


def run_script(
    cmd: str, chain_dir: str | None = None, task_id: str | None = None
) -> dict:
    """Run a shell command with safe interpolation and timeout.

    <chain>/<chain_dir> and <task_id> are interpolated with shlex.quote().
    Also passed as DELTA_CHAIN_DIR / DELTA_TASK_ID environment variables.
    Returns {"exit_code": int, "stdout": str, "stderr": str}.
    """
    env = os.environ.copy()
    if chain_dir:
        cmd = cmd.replace("<chain>", shlex.quote(chain_dir)).replace(
            "<chain_dir>", shlex.quote(chain_dir)
        )
        env["DELTA_CHAIN_DIR"] = chain_dir
    if task_id:
        cmd = cmd.replace("<task_id>", shlex.quote(task_id))
        env["DELTA_TASK_ID"] = task_id

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=_SCRIPT_TIMEOUT,
            env=env,
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": "Timeout ejecutando: " + cmd}
    except Exception as e:
        return {"exit_code": -1, "stdout": "", "stderr": str(e)}


def _is_chain_dir(path: str) -> bool:
    """Check if path looks like a valid sprint chain directory."""
    return os.path.isfile(os.path.join(path, "sprint-state.json"))


# ── Parsing utilities ───────────────────────────────────────────────────────


def _parse_args(args: list[str]) -> tuple[list[str], dict[str, str]]:
    """Split args into (positional, flags_dict)."""
    positional: list[str] = []
    flags: dict[str, str] = {}
    skip_next = False
    for i, a in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if a.startswith("--"):
            key = a[2:]
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                flags[key] = args[i + 1]
                skip_next = True
            else:
                flags[key] = ""
        else:
            positional.append(a)
    return positional, flags


# ── Workflow navigation helpers ─────────────────────────────────────────────


def _get_current_node(state: dict) -> str:
    """Get current node name with backward compat for v3 state files."""
    return state.get("current_node", state.get("current_state", "SPRINT"))


def _get_current_sub_key(state: dict) -> str:
    """Get current sub-state key with backward compat."""
    return state.get("current_sub_state", "INIT")


def _get_sub_state(workflow: dict, node_id: str, sub_key: str) -> dict | None:
    """Get sub-state config by key from a node."""
    node = workflow.get("nodes", {}).get(node_id, {})
    for ss in node.get("sub_states", []):
        if ss.get("key") == sub_key:
            return ss
    return None


def _get_current_sub_state(workflow: dict, state: dict) -> dict | None:
    """Get current sub-state config from walker state."""
    node_id = _get_current_node(state)
    sub_key = _get_current_sub_key(state)
    return _get_sub_state(workflow, node_id, sub_key)


def _get_sub_state_by_index(workflow: dict, node_id: str, idx: int) -> dict | None:
    """Get sub-state at given index in a node."""
    node = workflow.get("nodes", {}).get(node_id, {})
    sub_states = node.get("sub_states", [])
    if 0 <= idx < len(sub_states):
        return sub_states[idx]
    return None


def _get_node_sub_states(workflow: dict, node_id: str) -> list[dict]:
    """Get all sub-states for a node."""
    node = workflow.get("nodes", {}).get(node_id, {})
    return node.get("sub_states", [])


def _get_required_fields(workflow: dict, state: dict) -> list[str]:
    """Get required_fields for current sub-state."""
    ss = _get_current_sub_state(workflow, state)
    if ss:
        return ss.get("required_fields", [])
    return []


def _get_inter_node_edges(workflow: dict, node_id: str) -> list[dict]:
    """Get inter-node edges FROM a given node (for display and navigation)."""
    result = []
    for edge in workflow.get("edges", []):
        if edge["from"] == node_id:
            result.append(edge)
    return result


def _valid_transitions(workflow: dict, node_id: str) -> list[dict]:
    """Return valid inter-node transitions from a given node."""
    result = []
    for edge in workflow.get("edges", []):
        if edge["from"] == node_id:
            target = workflow["nodes"].get(edge["to"], {})
            result.append(
                {
                    "to": edge["to"],
                    "label": target.get("label", edge["to"]),
                    "description": target.get("description", ""),
                    "gate": edge.get("gate", ""),
                    "gate_description": edge.get("description", ""),
                    "sub_state_gate": edge.get("sub_state_gate", ""),
                }
            )
    return result


# Backward-compat alias: external modules import valid_transitions (no underscore)
valid_transitions = _valid_transitions


def _find_target(workflow: dict, node_id: str, gate: str) -> str | None:
    """Find target node for a given gate from current node (inter-node edges)."""
    for edge in workflow.get("edges", []):
        if edge["from"] == node_id and edge.get("gate") == gate:
            return edge["to"]
    return None


def _load_tasks(chain_dir: str) -> tuple[list[dict], str]:
    """Load task list from plan.json. Returns (tasks, error_msg)."""
    plan_path = os.path.join(chain_dir, "plan.json")
    if not os.path.exists(plan_path):
        return [], "plan.json no encontrado."
    pd = load_json(plan_path)
    if not pd:
        return [], "Error leyendo plan.json."
    return pd.get("plan", pd).get("tasks", []), ""


# ── Constants ───────────────────────────────────────────────────────────────

_DONE_STATUSES = {"completed", "achieved", "cancelled", "closed", "done"}
_INIT_SCRIPT = ""
_V4_NODES = ["SPRINT", "ANALYZE", "IMPLEMENT", "VERIFY", "REFINE"]
_V4_EDGES = [
    ("SPRINT", "ANALYZE", "start_task"),
    ("SPRINT", "SPRINT", "edit_plan"),
    ("SPRINT", "SPRINT", "close_sprint"),
    ("SPRINT", "SPRINT", "back_to_active"),
    ("ANALYZE", "IMPLEMENT", "next_step"),
    ("ANALYZE", "IMPLEMENT", "start_implement"),
    ("ANALYZE", "SPRINT", "escalate"),
    ("ANALYZE", "ANALYZE", "set_context"),
    ("IMPLEMENT", "VERIFY", "implement_ready"),
    ("VERIFY", "SPRINT", "close_task"),
    ("VERIFY", "IMPLEMENT", "needs_revision"),
    ("VERIFY", "SPRINT", "all_done"),
]
_V3_NODE_NAMES = ["SPRINT", "ANALYZE", "IMPLEMENT", "VERIFY", "REFINE"]

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_SCRIPT_DIR)  # libs/
_PROJECTS_DIR = ".cognitive/projects"
_DELTA_DIR = "delta"
_SCRIPTS_DIR = os.path.join(_BASE_DIR, "scripts")
_SPRING_SCRIPT = os.path.join(_SCRIPTS_DIR, "spring.py")
_DELTA_GATE_SCRIPT = os.path.join(_SCRIPTS_DIR, "delta-gate-check.py")
_TASK_SCRIPT = os.path.join(_SCRIPTS_DIR, "task.py")
_INIT_SCRIPT = ""  # DEPRECATED: delta-init-project.py eliminado (TASK-011)
_DONE_STATUSES = {"completed", "achieved", "cancelled", "closed", "done"}
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


def canonical_workflow_path(project_id: str | None = None) -> str:
    """Deriva la ruta al workflow del proyecto (workflow.v3/workflow.json).

    El workflow del motor SIEMPRE se carga desde la plantilla canónica
    workflow.v3. Si un proyecto tiene la suya propia, se usa; si no,
    el motor usa la plantilla canónica. No existe fallback hardcoded.
    """
    from libs.path_resolver import get_workflow_dir
    pid = project_id or ""
    if not pid:
        return ""
    return str(get_workflow_dir(pid) / "workflow.json")


def _resolve_chain_id(chain_id: str) -> str | None:
    """Resolve a chain_id (e.g. 'workflow-engine-v2-20260723') to its full path.

    Scans .cognitive/projects/*/data/spring/*/ for matching chain directories.
    Supports 'project_id:chain_id' format for disambiguation.
    Returns the full path or None if not found.
    """
    from libs.path_resolver import get_projects_dir

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
    # Scanea el layout canónico: <projects_dir>/<pid>/data/<sprint_dir>/*/sprint-state.json
    pattern = os.path.join(projects_dir, "*", "data", "*", "*", "sprint-state.json")
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
    from libs.path_resolver import get_delta_dir

    # Intenta derivar project_id del path o usarlo como delta dir directamente
    try:
        delta_dir = str(get_delta_dir(os.path.basename(project_path)))
    except Exception:
        delta_dir = ""
    if not delta_dir or not os.path.isdir(delta_dir):
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
    schema_path = os.path.join(_BASE_DIR, "..", "schema", "plan.schema.json")
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
    from libs.path_resolver import resolve_project_root

    return os.path.join(resolve_project_root(chain_dir), "backlog.json")


def _project_dir(chain_dir: str) -> str:
    """Derive project directory from a chain_dir."""
    from libs.path_resolver import resolve_project_root

    return resolve_project_root(chain_dir)


def _find_cognitive_root() -> str | None:
    """Find the .cognitive directory by searching upward from cwd."""
    from libs.path_resolver import find_cognitive_root as _find

    result = _find()
    return str(result) if result else None


def _find_active_sprints() -> list[dict]:
    """Scan all projects for active sprints with walker state."""
    from libs.path_resolver import get_projects_dir

    projects_dir = str(get_projects_dir())
    if not os.path.isdir(projects_dir):
        return []

    results = []
    seen_chains = set()

    for sprint_state_path in glob.glob(
        os.path.join(projects_dir, "*", "data", "*", "*", "sprint-state.json")
    ):
        chain_dir = os.path.dirname(sprint_state_path)
        parts = chain_dir.split(os.sep)
        # parts = [..., "projects", "<pid>", "data", "<sprint_dir>", "<cid>"]
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
