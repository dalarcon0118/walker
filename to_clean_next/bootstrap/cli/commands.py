"""cli/commands.py — Handlers de comandos CLI.

Responsabilidad: funciones cmd_* que manejan cada subcomando.
Cada función recibe args (list[str]) y retorna exit_code (int).
"""

from __future__ import annotations
import json
import os

from features.engine.domain.session import SessionRecord, SessionStore
from bootstrap.cli.utils import _safe, _output, _get_api, _ensure_qlty_for_project
from bootstrap.cli.project import (
    _find_sprint,
    _list_projects,
    _show_projects_dashboard,
    _show_project_status,
    _show_human_dashboard,
)
from bootstrap.cli.prompt import _AGENT_PROMPT
from libs.path_resolver import (
    get_project_dir,
    get_projects_dir,
    get_manifest_path,
    get_repo_path,
)


# ─── Lazy bootstrap (cached singletons) ──────────────────────────────────────
_registry_instance = None


def _get_task_registry():
    """Retorna el TaskRegistry singleton (lazy bootstrap, cached)."""
    global _registry_instance
    if _registry_instance is not None:
        return _registry_instance
    from features.engine.infrastructure.task_registry import (
        InMemoryTaskRegistry,
    )
    from _walker.workflow_engine.bootstrap import bootstrap_tasks

    registry = InMemoryTaskRegistry()
    bootstrap_tasks(registry)
    _registry_instance = registry
    return _registry_instance


# ─── Config ──────────────────────────────────────────────────────────────────
DEFAULT_INTERACTIVE_PROMPT = "walker3> "


def cmd_prepare(args: list[str]) -> int:
    """Initialize .cognitive/ in the current directory.

    Creates the directory structure and config for the walker
    to operate from this project, like 'git init' but for knowledge.

    Usage:
        walker3 prepare [--name <project-name>]
    """
    from pathlib import Path

    cwd = Path.cwd().resolve()
    cognitive_dir = cwd / ".cognitive"

    # Detect if already exists
    if cognitive_dir.is_dir():
        print(f"  ⚠️  .cognitive/ already exists in {cwd}")
        print("  Use 'walker3 status' to see project status.")
        return 1

    # Project name
    project_name = cwd.name
    if args and args[0] == "--name" and len(args) > 1:
        project_name = args[1]

    print(f"\n  🛠  Preparing walker in {cwd}/.cognitive/")
    print(f"  Project: {project_name}")
    print()

    # Create structure
    cognitive_dir.mkdir(parents=True, exist_ok=True)
    (cognitive_dir / "projects" / project_name / "data").mkdir(
        parents=True, exist_ok=True
    )
    (cognitive_dir / "projects" / project_name / "delta").mkdir(
        parents=True, exist_ok=True
    )
    (cognitive_dir / "sessions").mkdir(parents=True, exist_ok=True)

    # Create .cognitive/config.json
    config = {
        "projects_dir": str(cognitive_dir / "projects"),
        "sessions_dir": str(cognitive_dir / "sessions"),
        "debug_level": 0,
    }
    config_path = cognitive_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2))

    # Create .cognitive/projects/{project}/project.json
    import datetime as dt

    project_manifest = {
        "project": {
            "meta": {
                "project_id": project_name,
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            },
            "identity": {
                "name": project_name,
                "description": f"Walker project: {project_name}",
            },
            "location": {
                "repo_path": str(cwd),
                "code_paths": ["."],
            },
        }
    }

    manifest_path = cognitive_dir / "projects" / project_name / "project.json"
    manifest_path.write_text(json.dumps(project_manifest, indent=2))

    print(f"  ✅ .cognitive/ created at: {cognitive_dir}")
    print(f"  📁 Project: {project_name}")
    print(f"  📄 Config:   {config_path}")
    print(f"  📄 Manifest: {manifest_path}")
    print()
    print("  ▶️  Next step: walker3 status")
    return 0


def cmd_list(args: list[str]) -> int:
    """List projects (no prefix) or sprints (with project_id).

    walker3 list                    → projects
    walker3 agents/<id>/list       → projects (multi-tenant)
    walker3 <project>/list          → sprints of a project
    """
    if not args:
        projects = _list_projects()
        return _show_projects_dashboard(projects)

    pid = args[0]
    projects = _list_projects()
    for p in projects:
        if p["project_id"] != pid:
            continue
        print(f"  Sprints for {pid} ({len(p['sprints'])}):")
        print()
        for s in p["sprints"]:
            icon = ""
            if s["status"] == "active":
                icon = "▶ "
            elif s["status"] == "closed":
                icon = "✓ "
            else:
                icon = "  "
            print(f"  {icon}{s['id']}  [{s['status']}]")
        return 0

    print(f"Project not found: {pid}")
    return 1


def cmd_status(args: list[str]) -> int:
    """Muestra el estado del workflow.

    walker3 status              → dashboard multitenant
    walker3 <project>/status    → estado de un proyecto
    walker3 <project>/<sprint>/status → estado de un sprint
    walker3 agents/<id>/status  → dashboard
    """
    # Sin argumentos: dashboard multitenant
    if not args:
        projects = _list_projects()
        return _show_projects_dashboard(projects)

    # Un argumento: puede ser project_id o ruta agents/<id>/status
    # Si es agents/<id>/status, lo tratamos como dashboard
    # Si es un project_id, mostramos estado del proyecto
    pid = args[0]
    if len(args) == 1:
        # Verificar si es project_id conocido
        projects = _list_projects()
        for p in projects:
            if p["project_id"] == pid:
                return _show_project_status(pid)
        # Si no se encontró, tratar como dashboard
        return _show_projects_dashboard(projects)

    # Dos argumentos: <project>/<sprint>/status
    sprint_hint = args[1]
    if not _ensure_project_configured(pid, sprint_hint):
        return 1
    api = _get_api(pid, sprint_hint)
    _output(api.status())
    return 0


def _try_parse_json(val: str):
    """Intenta parsear un string como JSON. Si falla, retorna el string original."""
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val


def cmd_submit(args: list[str]) -> int:
    """Envía datos al sub-estado actual.

    walker3 <project>/<sprint>/submit --k1 v1 --k2 v2
    walker3 agents/<id>/<project>/<sprint>/submit ...

    Los valores que parezcan JSON (arrays, objetos) se parsean automáticamente.
    """
    if len(args) < 2:
        print("USAGE: <project>/<sprint>/submit --key val [--key val ...]")
        return 1

    pid = args[0]
    sprint_hint = args[1]
    # Extraer kwargs de args[2:]
    kwargs = {}
    i = 2
    while i < len(args):
        if args[i].startswith("--"):
            key = args[i][2:]
            i += 1
            if i < len(args) and not args[i].startswith("--"):
                kwargs[key] = _try_parse_json(args[i])
                i += 1
            else:
                kwargs[key] = True
        else:
            i += 1

    api = _get_api(pid, sprint_hint)
    _output(_safe(api.submit(**kwargs)))
    return 0


def cmd_advance(args: list[str]) -> int:
    """Avanza al siguiente sub-estado dentro del mismo nodo.

    walker3 <project>/<sprint>/advance
    walker3 agents/<id>/<project>/<sprint>/advance
    """
    if len(args) < 2:
        print("USAGE: <project>/<sprint>/advance")
        return 1
    pid = args[0]
    sprint_hint = args[1]
    api = _get_api(pid, sprint_hint)
    _output(_safe(api.advance()))
    return 0


def cmd_transition(args: list[str]) -> int:
    """Transiciona a un nuevo nodo del workflow.

    walker3 <project>/<sprint>/transition <gate>
    walker3 agents/<id>/<project>/<sprint>/transition <gate>
    """
    if len(args) < 3:
        print("USAGE: <project>/<sprint>/transition <gate>")
        return 1
    pid = args[0]
    sprint_hint = args[1]
    gate = args[2]
    extra_args = args[3:]

    kwargs = {}
    i = 0
    while i < len(extra_args):
        if extra_args[i].startswith("--"):
            key = extra_args[i][2:]
            i += 1
            if i < len(extra_args) and not extra_args[i].startswith("--"):
                kwargs[key] = extra_args[i]
            else:
                kwargs[key] = True
        else:
            i += 1

    api = _get_api(pid, sprint_hint)
    _output(_safe(api.transition(gate, **kwargs)))
    return 0


def cmd_session(args: list[str]) -> int:
    """Muestra la sesion activa actual."""
    session_id = os.environ.get("WALKER_SESSION_ID", "")
    agent_id = os.environ.get("WALKER_AGENT_ID", "")
    record = SessionStore.load(session_id) if session_id else None

    if record:
        result = {
            "session_id": record.session_id,
            "agent_id": agent_id or record.get_metadata("agent_id", ""),
            "project_id": record.project_id or "",
            "chain_dir": record.chain_dir or "",
            "status": record.status or "active",
            "created_at": record.created_at or "",
            "updated_at": record.updated_at or "",
        }
    else:
        result = {
            "session_id": session_id or "(ninguna)",
            "agent_id": agent_id or "(ninguno)",
            "project_id": "(no session)",
            "status": "inactive",
        }
    _output(result)
    return 0


def cmd_config(args: list[str]) -> int:
    """Muestra la configuracion actual del walker."""
    import subprocess
    from libs.path_resolver import get_repo_path

    session_id = os.environ.get("WALKER_SESSION_ID", "")
    agent_id = os.environ.get("WALKER_AGENT_ID", "")
    cwd = os.getcwd()
    repo_root = get_repo_path("")

    # Ruta de qlty
    try:
        qlty_path = subprocess.check_output(["which", "qlty"], text=True).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        qlty_path = "(no instalado)"

    config_info = {
        "version": "v3",
        "walker_root": str(get_projects_dir()),
        "cwd": cwd,
        "repo_root": repo_root or "(no detectado)",
        "qlty_path": qlty_path,
        "session_id": session_id or "(ninguna)",
        "agent_id": agent_id or "(ninguno)",
        "env": {
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
            "WALKER_SESSION_ID": session_id,
            "WALKER_AGENT_ID": agent_id,
            "WALKER_DIR": os.environ.get("WALKER_DIR", ""),
        },
    }
    _output(config_info)
    return 0


def cmd_workflow(args: list[str]) -> int:
    """Muestra el workflow completo (nodos, edges, sub-estados).

    walker3 workflow                    → workflow global
    walker3 <project>/workflow          → workflow de un proyecto
    walker3 <project>/<sprint>/workflow → workflow de un sprint
    walker3 agents/<id>/.../workflow    → multi-tenant
    """
    if not args:
        # Workflow global (sin proyecto)
        api = _get_api()
        result = api.workflow()
        _output(result)
        return 0

    pid = args[0]
    if len(args) == 1:
        # Workflow de proyecto
        api = _get_api(pid)
        result = api.workflow()
        _output(result)
        return 0

    # Workflow de sprint
    sprint_hint = args[1]
    if not _ensure_project_configured(pid, sprint_hint):
        return 1
    api = _get_api(pid, sprint_hint)
    result = api.workflow()
    _output(result)
    return 0


def _ensure_project_configured(pid: str, sprint_id: str | None = None) -> bool:
    """Validate the project (and optionally the sprint) exist.

    Checks:
    1. project.json exists in .cognitive/projects/{pid}/
    2. If sprint_id, the delta directory exists

    Does NOT validate repo_path — that's _ensure_code_accessible.
    """
    manifest = get_manifest_path(pid)
    if not manifest.exists():
        os.getcwd()
        print(f"  ❌ Project '{pid}' is not configured.")
        print(f"  Expected project.json at:")
        print(f"     {manifest}")
        print()
        print("  ▶  Initialize the project:")
        print(f"       walker prepare --name {pid}")
        print(f"     (from the directory where your code is)")
        return False

    # Validate sprint if specified
    if sprint_id:
        from libs.path_resolver import get_delta_dir

        sprint_dir = get_delta_dir(pid) / sprint_id
        if not sprint_dir.is_dir():
            print(f"  ❌ Sprint '{sprint_id}' does not exist in project '{pid}'.")
            print(f"  Expected: {sprint_dir}")
            print()
            print(f"  ▶  Available sprints in '{pid}':")
            delta_dir = get_delta_dir(pid)
            if delta_dir.is_dir():
                sprints = sorted([d.name for d in delta_dir.iterdir() if d.is_dir()])
                for s in sprints:
                    print(f"       - {s}")
            print()
            print("  ▶  To create a sprint:")
            print(f"       walker3 {pid}/task/create_first_sprint")
            return False

    return True


def _ensure_code_accessible(pid: str) -> bool:
    """Ensure the project's source code is accessible on disk.

    Checks that repo_path exists on disk.
    If CWD doesn't match, warns but doesn't block code-reading tasks.
    """
    manifest = get_manifest_path(pid)
    repo = get_repo_path(pid)
    cwd = os.getcwd()

    if not repo:
        print(f"  ⚠️  Project '{pid}' has no repo_path in project.json.")
        print(f"  Edit {manifest} and add:")
        print(f'     "location": {{ "repo_path": "{cwd}" }}')
        return False

    if not os.path.isdir(repo):
        print(f"  ⚠️  repo_path for project '{pid}' does not exist on disk:")
        print(f"     {repo}")
        print()
        print("  ▶  Go to the directory where the code is:")
        print(f"       cd {repo}")
        print(f"  Or edit repo_path in {manifest}")
        return False

    if repo != cwd and not cwd.startswith(repo + "/"):
        print(f"  ⚠️  You are not in the project '{pid}' directory.")
        print(f"  Project expects code at: {repo}")
        print(f"  Your CWD is: {cwd}")
        print(f"  ▶  cd {repo}")
        return False

    return True


def cmd_task(args: list[str]) -> int:
    """Ejecuta una tarea directamente (salta el workflow).

    walker3 <project>/task/<task_name> --key val ...
    walker3 <project>/<sprint>/task/<task_name> --key val ...
    walker3 agents/<id>/<project>/<sprint>/task/<task_name> --key val ...

    Args: [project_id, sprint_id?, task_name, --key val ...]
    """
    if len(args) < 2:
        print("USAGE: <project>/task/<task_name> [--key val ...]")
        print("       <project>/<sprint>/task/<task_name> [--key val ...]")
        print("Available tasks: create_first_sprint, plan_sprint, spec_task, etc.")
        return 1

    # Detectar si hay sprint_id (3+ args antes de flags)
    # Formato: [project_id, sprint_id, task_name] o [project_id, task_name]
    pid = args[0]
    sprint_id = None
    task_name = None
    kw_start = 1

    # Determinar formato: [pid, task_name] o [pid, sprint_id, task_name]
    if len(args) >= 3 and not args[2].startswith("--"):
        # 3+ args antes de flags: puede ser [pid, sprint_id, task_name] o [pid, task_name, extra]
        registry = _get_task_registry()
        known_tasks = registry.list_tasks()

        if args[1] in known_tasks:
            # Formato sin sprint: [pid, task_name, extra_args...]
            pid = args[0]
            task_name = args[1]
            kw_start = 2
        else:
            # Formato con sprint: [pid, sprint_id, task_name, extra_args...]
            pid = args[0]
            sprint_id = args[1]
            task_name = args[2]
            kw_start = 3
    elif len(args) >= 2:
        # Formato simple: [pid, task_name]
        task_name = args[1]
        kw_start = 2

    if not task_name:
        print("USAGE: <project>/task/<task_name> [--key val ...]")
        return 1

    # Validar que el proyecto (y sprint si aplica) está configurado
    if not _ensure_project_configured(pid, sprint_id):
        return 1

    # Para tareas que leen código, validar acceso al código
    if task_name in (
        "scan_code_and_build_cpg",
        "build_initial_ckg",
        "cpg_index_ref",
        "kg_index_repo",
        "project_metrics",
        "review_code",
    ):
        if not _ensure_code_accessible(pid):
            return 1

    # Extraer kwargs posicionales
    kwargs = {}
    i = kw_start
    while i < len(args):
        if args[i].startswith("--"):
            key = args[i][2:]
            i += 1
            if i < len(args) and not args[i].startswith("--"):
                kwargs[key] = args[i]
                i += 1
            else:
                kwargs[key] = True
        else:
            i += 1

    # Asegurar qlty antes de tareas de análisis
    if task_name in ("create_first_sprint",):
        _ensure_qlty_for_project(str(get_project_dir(pid)))

    registry = _get_task_registry()
    task_cls = registry.get_task(task_name)
    if not task_cls:
        print(f"Task not found: {task_name}")
        print(f"Available tasks: {', '.join(sorted(registry.list_tasks()))}")
        return 1

    # Crear ExecutionContext para la tarea
    from features.engine.domain.models import ExecutionContext
    import uuid

    if sprint_id:
        chain_dir = str(get_project_dir(pid) / "delta" / sprint_id)
    else:
        chain_dir = str(get_project_dir(pid))
    ctx = ExecutionContext.create(
        session_id=os.environ.get("WALKER_SESSION_ID", "cli"),
        chain_dir=chain_dir,
        task_id=task_name,
        trace_id=uuid.uuid4().hex[:12],
    )

    kwargs.setdefault("project_id", pid)
    if sprint_id:
        kwargs.setdefault("sprint_id", sprint_id)
    # Las tareas registradas tienen firma fn(ctx, data) -> TaskResult
    result = task_cls(ctx, kwargs)
    print(json.dumps(_safe(result), indent=2, ensure_ascii=False))
    return 0


def cmd_tasks(args: list[str]) -> int:
    """Lista tareas disponibles o muestra ayuda de una.

    walker3 tasks                    → lista todas
    walker3 agents/<id>/tasks       → lista todas (multi-tenant)
    walker3 tasks/<task_name>       → ayuda de una tarea
    """
    registry = _get_task_registry()
    if not args:
        print("Registered tasks:")
        for name in sorted(registry.list_tasks()):
            print(f"  - {name}")
        print()
        print("Usa: walker3 tasks/<task_name> para ayuda detallada")
        return 0

    task_name = args[0]
    task_fn = registry.get_task(task_name)
    if not task_fn:
        print(f"Task not found: {task_name}")
        return 1

    print(f"Task: {task_name}")
    print(f"  {task_fn.__doc__ or '(sin descripción)'}")
    # task_fn es una función que recibe (ctx, data), no una clase
    # Mostramos metadata si existe
    info = registry.get_task_info(task_name)
    if info:
        if info.get("input_schema"):
            print(
                f"  Input schema: {json.dumps(_safe(info['input_schema']), indent=4)}"
            )
        if info.get("output_schema"):
            print(
                f"  Output schema: {json.dumps(_safe(info['output_schema']), indent=4)}"
            )
        if info.get("example"):
            print(f"  Ejemplo: {json.dumps(_safe(info['example']), indent=4)}")
    return 0


def cmd_switch(args: list[str]) -> int:
    """Cambia la sesión activa a un proyecto/sprint.

    walker3 switch <project_id>
    walker3 switch <project_id>/<sprint_id>
    walker3 agents/<id>/switch <project_id>/<sprint_id>
    """
    if not args:
        _list_projects()
        return _show_human_dashboard()

    target = args[0]  # "project" o "project/sprint"
    parts = target.split("/")
    pid = parts[0]
    sprint_hint = parts[1] if len(parts) > 1 else ""
    chain_dir = _find_sprint(pid, sprint_hint) if pid else None

    record = SessionRecord.create(
        project_id=pid,
        chain_dir=chain_dir or "",
    )
    record.ensure_session_dir()
    SessionStore.save(record)
    SessionStore.set_active(record.session_id)

    print(f"  Session switched to: {pid}{'/' + sprint_hint if sprint_hint else ''}")
    print(f"  Session ID: {record.session_id}")
    if chain_dir:
        print(f"  Chain dir: {chain_dir}")
    return 0


def cmd_help(args: list[str]) -> int:
    """Show the complete walker guide.

    AUTO-GENERATED and DYNAMIC:
    - Tasks loaded from TaskRegistry (tasks/*.py)
    - Workflow loaded from WorkflowRepository (workflow.json)
    - If tasks, nodes or gates are added tomorrow, the help includes
      them automatically without modifying this command.
    - The static meta-guide teaches the agent HOW to discover info.
    - CONTEXTUAL: detects active session and shows next actionable steps.
    """
    # ── 0. Contextual next steps ─────────────────────────────────
    print()
    print("╔" + "═" * 58 + "╗")
    print("║   " + "WALKER3 — NEXT STEPS".ljust(53) + "║")
    print("╚" + "═" * 58 + "╝")
    print()

    raw_agent_id = os.environ.get("WALKER_AGENT_ID", "")
    if not raw_agent_id:
        # ── Agent NOT identified ──
        print("  🔴 You have not identified yourself as an agent.")
        print()
        print("  ▶️  FIRST STEP: Identify yourself.")
        print()
        print("     The walker needs to know which agent you are to manage")
        print("     multi-tenant sessions. Set WALKER_AGENT_ID or use:")
        print()
        print("     walker3 agents/<your-id>/status")
        print()
        print("     Example:")
        print("     walker3 agents/codebuff/status")
        print("     walker3 agents/opencode/status")
        print()
        print("  💡 Replace <your-id> with your agent identifier.")
        print("     This automatically configures the session.")
        print()
    else:
        # ── El agente SÍ está identificado ──
        agent_id = raw_agent_id
        record = SessionStore.get_active()

        if record and record.project_id and record.chain_dir:
            # ── Hay sesión activa: mostrar estado contextual ──
            try:
                chain_dir = record.chain_dir
                project_id = record.project_id
                sprint_name = os.path.basename(chain_dir)

                api = _get_api(project_id, sprint_name)
                st = api.status()

                if isinstance(st, dict):
                    node = st.get("current_node") or st.get("node") or "?"
                    sub = st.get("current_sub_state") or st.get("sub_state") or ""
                    mode = st.get("execution_type") or st.get("mode") or "manual"

                    print(f"  ✅ Agent:    {agent_id}")
                    print(f"  📍 Proyecto:  {project_id}")
                    print(f"  📂 Sprint:    {sprint_name}")
                    print(f"  🎯 Nodo:      {node}.{sub}")
                    print(f"  ⚙️  Modo:     {mode}")
                    print()

                    if mode == "auto":
                        print(
                            "  ⚡ El engine está ejecutando este paso automáticamente."
                        )
                        print("  → Solo espera a que termine.")
                    elif mode == "llm_task":
                        required = st.get("required_fields", [])
                        print("  🤖 REQUIERE TU DECISIÓN (llm_task).")
                        print()
                        if required:
                            print("  📋 Envía estos datos con submit:")
                            for f in required:
                                print(f"     • --{f}  <valor>")
                            print()
                        else:
                            print(
                                "  📋 No requiere campos específicos. Usa submit genérico."
                            )
                            print()
                        print(
                            f"  ▶️  walker3 agents/{agent_id}/{project_id}/{sprint_name}/submit --campo valor"
                        )
                    elif mode == "user_input":
                        required = st.get("required_fields", [])
                        print("  👤 REQUIERE TU RESPUESTA (user_input).")
                        print()
                        if required:
                            print("  📋 Campos requeridos:")
                            for f in required:
                                print(f"     • --{f}  <valor>")
                            print()
                        print(
                            f"  ▶️  walker3 agents/{agent_id}/{project_id}/{sprint_name}/submit --decision approve|reject"
                        )

                    # Gates disponibles
                    gates = st.get("available_gates", [])
                    if gates:
                        print()
                        print("  🚪 Gates disponibles:")
                        for g in gates:
                            print(f"     • [{g['name']}] → {g.get('target', '?')}")
                        print()
                        print(
                            f"  ▶️  walker3 agents/{agent_id}/{project_id}/{sprint_name}/transition <gate>"
                        )

                    print()
                    print(
                        f"  📖 walker3 agents/{agent_id}/{project_id}/{sprint_name}/status  — Estado detallado"
                    )
                    print(
                        f"  📖 walker3 agents/{agent_id}/{project_id}/workflow            — Workflow completo"
                    )
                else:
                    print(f"  ✅ Agent: {agent_id}")
                    print(f"  📍 Sesión activa: {project_id}/{sprint_name}")
                    print("  ▶️  Usa 'status' para ver tu posición actual")

            except Exception as e:
                print(f"  ✅ Agent: {agent_id}")
                print(f"  📍 Sesión activa: {record.project_id}")
                print(f"     (No se pudo leer el estado: {e})")
                print()
                print("  ▶️  Intenta:")
                print(f"     walker3 agents/{agent_id}/{record.project_id}/status")
                print(f"     walker3 agents/{agent_id}/{record.project_id}/workflow")
                print()
        else:
            # ── Sin sesión activa: mostrar cómo empezar ──
            print(f"  ✅ Agent: {agent_id}")
            print()
            print("  🔴 You have no active session.")
            print()
            print("  ▶️  NEXT STEP: Choose a project to start.")
            print()
            print(
                f"     walker3 agents/{agent_id}/list             ← View available projects"
            )
            print(
                f"     walker3 agents/{agent_id}/<project>/list   ← View sprints of a project"
            )
            print(
                f"     walker3 agents/{agent_id}/switch <project>/<sprint>  ← Activate session"
            )
            print()
            print("  📋 Available projects:")
            for p in _list_projects():
                active_count = len(
                    [s for s in p.get("sprints", []) if s["status"] == "active"]
                )
                status = "▶ active" if active_count > 0 else "  inactive"
                print(f"     {status}  {p['project_id']}")
            print()
            print("  💡 If this is your first time, start with:")
            print(f"     walker3 agents/{agent_id}/status    ← General dashboard")
            print()

    # ── Separator: contextual guidance above, reference below ────
    print()
    print("─" * 60)
    print("  📖 REFERENCIA: guía completa a continuación")
    print(
        "  (La sección de arriba es tu próxima acción — lo de abajo es documentación)"
    )
    print("─" * 60)
    print()

    # ── 1. Static meta-guide ─────────────────────────────────────
    print(_AGENT_PROMPT)

    # ── 2. Dynamic tasks ─────────────────────────────────────────
    print()
    print("═" * 60)
    print("  AVAILABLE TASKS (auto-discovered from TaskRegistry)")
    print("═" * 60)
    print()
    print("  These tasks are auto-discovered on startup from tasks/*.py.")
    print("  If a new task is added tomorrow, it appears here automatically.")
    print()

    registry = _get_task_registry()
    for name in sorted(registry.list_tasks()):
        task_fn = registry.get_task(name)
        info = registry.get_task_info(name)
        print(f"  • {name}")

        # Full docstring (not just first line)
        doc = (task_fn.__doc__ or "").strip() if task_fn else ""
        if doc:
            for line in doc.split("\n"):
                print(f"    {line}")
        else:
            print("    (no description)")

        # Input schema (with descriptions)
        if info and info.get("input_schema"):
            inp = info["input_schema"]
            print("    Input schema:")
            # Required fields with their types and descriptions
            required = inp.get("required", [])
            if required:
                print(f"      Required: {', '.join(required)}")
            optional = inp.get("optional", [])
            if optional:
                print(f"      Optional: {', '.join(optional)}")
            # Field details (types, descriptions)
            # Required fields first, then optional
            for field_name in required:
                if field_name not in inp:
                    continue
                field_info = inp[field_name]
                ftype = field_info.get("type", "")
                fdesc = field_info.get("description", "")
                detail = f"      • {field_name}"
                if ftype:
                    detail += f"  [{ftype}]"
                if fdesc:
                    detail += f": {fdesc}"
                print(detail)
                # Show example if present
                fexample = field_info.get("example")
                if fexample:
                    print(f"         example: {json.dumps(fexample)}")
            for field_name in optional:
                if field_name not in inp or field_name in required:
                    continue
                field_info = inp[field_name]
                ftype = field_info.get("type", "")
                fdesc = field_info.get("description", "")
                detail = f"      • {field_name}"
                if ftype:
                    detail += f"  [{ftype}]"
                if fdesc:
                    detail += f": {fdesc}"
                print(detail)
                # Show example if present
                fexample = field_info.get("example")
                if fexample:
                    print(f"         example: {json.dumps(fexample)}")

        # Output schema (full)
        if info and info.get("output_schema"):
            out = info["output_schema"]
            print("    Output schema:")
            for line in json.dumps(out, indent=2).split("\n"):
                print(f"      {line}")

        # Example (full)
        if info and info.get("example"):
            ex = info["example"]
            print("    Example:")
            ex_str = json.dumps(ex, indent=2) if isinstance(ex, dict) else str(ex)
            for line in ex_str.split("\n"):
                print(f"      {line}")

        print()

    # ── 3. Dynamic workflow ───────────────────────────────────────
    print()
    print("═" * 60)
    print("  WORKFLOW MAP (auto-discovered from repository)")
    print("═" * 60)
    print()
    print("  This map is generated from workflow.json. If the workflow changes,")
    print("  this map changes automatically — no help update required.")
    print()

    try:
        from features.engine.infrastructure.repositories import (
            WorkflowRepository,
        )

        # Try to load workflow from active session
        record = SessionStore.get_active()
        wf_chain_dir = record.chain_dir if record else ""

        wf_repo = WorkflowRepository()
        graph = wf_repo.load(wf_chain_dir)

        if graph.nodes:
            for nid, node in graph.nodes.items():
                label = node.label or nid
                desc = node.description or ""
                print(f"  ── {nid} ({label}) ──")
                if desc:
                    print(f"     {desc}")

                # Sub-states
                for ss in node.sub_states:
                    exec_type = ss.execution.value if ss.execution else "manual"
                    required = (
                        ", ".join(ss.required_fields) if ss.required_fields else "—"
                    )
                    print(f"     • {ss.key}  [{exec_type}]")
                    if ss.label:
                        print(f"       {ss.label}")
                    print(f"       required_fields: {required}")
                    if ss.action:
                        print(f"       action: {ss.action}")
                    if ss.side_effects:
                        se_names = [se.type for se in ss.side_effects]
                        print(f"       side_effects: {', '.join(se_names)}")

                # Gates
                if node.gates:
                    print("     Gates:")
                    for g in node.gates:
                        target = g.target_node
                        if g.target_sub_state:
                            target += f".{g.target_sub_state}"
                        print(f"       → {g.name}:  {target}")
                        if g.description:
                            print(f"          {g.description}")
                print()

            # Initial state
            if graph.initial_state:
                print(f"  Initial node: {graph.initial_state}")

            # Event handlers
            if graph.on_events:
                print("  Event handlers (on:{}):")
                for evt_name, evt_cfg in graph.on_events.items():
                    handler = ""
                    if "run" in evt_cfg:
                        handler = f"run: {evt_cfg['run']}"
                    elif "transition" in evt_cfg:
                        handler = f"transition: {evt_cfg['transition']}"
                    print(f"     • {evt_name}  →  {handler}")

            # ── Flows narrative ────────────────────────────────────────
            print()
            print("  ── FLOWS (auto-discovered narrative) ──")
            print()
            flows = graph.generate_flows()
            if flows:
                for i, flow in enumerate(flows):
                    compact = flow.get("name", "")
                    ncount = flow.get("node_count", 0)
                    print(f"  Flow #{i + 1} ({ncount} nodes):")
                    print(f"    {compact}")
                    print()
                    # Detailed steps
                    for s in flow.get("steps", []):
                        if s["action"] == "process":
                            node = s.get("node", "")
                            ss = s.get("sub_state", "")
                            exec_type = s.get("execution", "")
                            label = s.get("label", ss)
                            req = s.get("required_fields", [])
                            req_str = (
                                f"  required_fields: {', '.join(req)}" if req else ""
                            )
                            print(
                                f"       {node}.{ss}  [{exec_type}]  {label}{req_str}"
                            )
                        elif s["action"] == "transition":
                            gate = s.get("gate", "")
                            target = s.get("to", "")
                            desc = s.get("description", "")
                            print(f"       ↓ [{gate}] → {target}")
                            if desc and desc != f"Transition via {gate}":
                                print(f"         {desc}")
                    print()
            else:
                print(
                    "  (No flows could be generated — graph may be empty or have no connections)"
                )
                print()
        else:
            print("  (No workflow loaded. Use 'walker3 workflow' with an")
            print("   active project/sprint to see the full map.)")
            print()
    except Exception as e:
        print(f"  (Could not load workflow: {e})")
        print()

    return 0
