"""
application/handlers.py — CLI use cases (thin wrappers).

Each handler:
  - Receives Request (structured REST request, SSOT del input)
  - Delegates to a use case in features/project/application/use_cases.py
  - Returns a Response (contrato REST con next_actions)
  - Does NOT print, does NOT do direct IO
  - Is resilient (does not propagate errors without handling)
"""

from __future__ import annotations

import os

from features.project import ProjectService
from features.project.application.use_cases import (
    PrepareProjectUseCase,
    CheckHealthUseCase,
)
from features.engine.application.workflow_api import WorkflowAPI
from libs.shared_domain import ExecutionContext
from ..infrastructure.contracts import Request, Response, action


def _safe_execute(fn, *args, **kwargs) -> Response:
    """Execute fn and capture errors into a Response estándar.

    SSOT for error handling across all handlers.
    Cada error se traduce a un Response REST con status_code HTTP:
      404 → recurso no encontrado, 400 → validación, 500 → interno.
    """
    try:
        return fn(*args, **kwargs)
    except FileNotFoundError as e:
        return Response.error(f"File not found: {e}", status_code=404)
    except ValueError as e:
        return Response.error(f"Validation error: {e}", status_code=400)
    except Exception as e:
        return Response.error(str(e), status_code=500)


def _sync_watcher(project_id: str) -> None:
    """Escanea cambios en el proyecto. Como git: siempre actualizado antes de operar.

    Se ejecuta al inicio de cada comando que opera sobre un proyecto,
    manteniendo el FileWatcher sincronizado sin necesidad de daemon.
    """
    if not project_id:
        return
    try:
        repo_path = _resolve_repo_path_from_registry(project_id)
        if repo_path and os.path.isdir(repo_path):
            from features.project.infrastructure.watcher import FileWatcher

            FileWatcher(repo_path).scan()
    except Exception:
        pass  # best-effort


def _ensure_chain_dir(project_id: str, sprint_id: str = "") -> str:
    """Resolve chain_dir. SSOT: delegates to ProjectService.

    Antes de resolver, sincroniza el watcher (como git status).
    """
    _sync_watcher(project_id)
    if not project_id:
        return ""
    if sprint_id:
        from libs.path_resolver import get_sprint_dir

        return str(get_sprint_dir(project_id, sprint_id))
    return ProjectService.find_active_sprint(project_id) or ""


def _list_from_global_registry() -> dict:
    """Read projects from global registry data/projects.json."""
    from libs.path_resolver import get_walker_root
    import json

    registry_path = get_walker_root() / "data" / "projects.json"
    if registry_path.exists():
        try:
            with open(registry_path) as f:
                registry = json.load(f)
            projects_list = []
            for pid, info in registry.get("projects", {}).items():
                projects_list.append(
                    {
                        "project_id": pid,
                        "name": info.get("name", pid),
                        "repo_path": info.get("repo_path", ""),
                        "status": "active" if info.get("active") else "inactive",
                    }
                )
            if projects_list:
                return {"type": "projects_list", "projects": projects_list}
        except Exception:
            pass
    return {"type": "projects_list", "projects": []}


def _list_from_global_registry_with_prompt() -> Response:
    """Show registered projects with create/select prompt."""
    result = _list_from_global_registry()
    result["prompt"] = (
        f"\n  No project found at '{os.getcwd()}'. Choose an option:\n"
        f"\n  1. Select an existing project → walker <project>/status\n"
        f"\n  2. Register a new project   → walker prepare .\n"
    )
    return Response.ok(
        "projects_list",
        message="No project detected at current directory",
        data=result,
        next_actions=[
            action("walker list", "see all registered projects"),
            action("walker prepare .", "register the current directory as a project"),
        ],
    )


def _resolve_repo_path_from_registry(project_id: str) -> str:
    """Look up a project's repo_path from the global registry."""
    from libs.path_resolver import get_walker_root
    import json

    registry_path = get_walker_root() / "data" / "projects.json"
    if registry_path.exists():
        try:
            with open(registry_path) as f:
                registry = json.load(f)
            projects = registry.get("projects", {})
            if project_id in projects:
                return projects[project_id].get("repo_path", "")
        except Exception:
            pass
    return ""


def _resolve_project_from_cwd(req: Request) -> bool:
    """SSOT: resuelve project_id desde el CWD (registry global) si falta.

    Se usa en TODOS los handlers que operan sobre un proyecto, para que
    `walker <comando>` funcione desde cualquier subdirectorio del proyecto
    sin especificar project_id (REST puro, sin flags).

    Returns:
        True si project_id quedó resuelto; False si no se encontró proyecto.
    """
    if req.project_id:
        return True
    resolved = ProjectService.resolve_from_registry()
    if not resolved:
        return False
    req.params["projectId"] = resolved["project_id"]
    req.set_repo_path(resolved.get("repo_path", ""))
    warning = resolved.get("_warning", "")
    if warning:
        req.flags["_legacy_warning"] = warning
    return True


def handle_project_info(req: Request) -> Response:
    """Use case: show all project information in one place."""

    def _exec():
        if not _resolve_project_from_cwd(req):
            return _list_from_global_registry_with_prompt()
        project_id = req.project_id
        repo_path = req.repo_path
        if not repo_path:
            repo_path = _resolve_repo_path_from_registry(project_id)
            req.set_repo_path(repo_path)

        info = {
            "type": "project_info",
            "project_id": project_id,
        }

        if not repo_path:
            return Response.error(
                f"Project '{project_id}' not found in registry",
                status_code=404,
                next_actions=[action("walker list", "see all registered projects")],
            )

        import json
        import os

        info["repo_path"] = repo_path

        # ── Registry data ──
        from libs.path_resolver import get_walker_root

        registry_path = get_walker_root() / "data" / "projects.json"
        if registry_path.exists():
            try:
                with open(registry_path) as f:
                    reg = json.load(f).get("projects", {}).get(project_id, {})
                    info["name"] = reg.get("name", project_id)
                    info["registered_at"] = reg.get("registered_at", "")
                    info["last_accessed"] = reg.get("last_accessed", "")
            except Exception:
                pass

        # ── Project metadata ──
        cog_data_dir = os.path.join(
            repo_path, ".cognitive", "projects", project_id, "data"
        )
        proj_json = os.path.join(cog_data_dir, "project.json")
        if not os.path.exists(proj_json):
            proj_json = os.path.join(repo_path, "project.json")
        if os.path.exists(proj_json):
            try:
                with open(proj_json) as f:
                    pdata = json.load(f)
                proj = pdata.get("project", pdata)
                identity = proj.get("identity", {})
                info["language"] = identity.get("language", "")
                info["framework"] = identity.get("framework", "")
                info["code_paths"] = identity.get("code_paths", [])
            except Exception:
                pass

        # ── Memory stats ──
        if os.path.isdir(cog_data_dir):
            # CPG
            cpg_path = os.path.join(cog_data_dir, "cpg.json.gz")
            if os.path.exists(cpg_path):
                import gzip

                try:
                    cpg_data = json.loads(
                        gzip.decompress(open(cpg_path, "rb").read()).decode()
                    )
                    info["cpg_nodes"] = len(cpg_data.get("nodes", {}))
                    info["cpg_edges"] = len(cpg_data.get("edges", []))
                except Exception:
                    pass

            # CKG
            ckg_path = os.path.join(cog_data_dir, "ckg.json.gz")
            if os.path.exists(ckg_path):
                try:
                    ckg_data = json.loads(
                        gzip.decompress(open(ckg_path, "rb").read()).decode()
                    )
                    docs = ckg_data.get("docs", [])
                    info["ckg_docs"] = len(docs)
                except Exception:
                    pass

            # Delta
            delta_path = os.path.join(cog_data_dir, "delta.json.gz")
            if os.path.exists(delta_path):
                try:
                    delta_data = json.loads(
                        gzip.decompress(open(delta_path, "rb").read()).decode()
                    )
                    info["delta_tasks"] = len(delta_data.get("docs", []))
                except Exception:
                    pass

            # Change detected
            change_path = os.path.join(cog_data_dir, "change_detected.json")
            if os.path.exists(change_path):
                try:
                    info["watcher"] = json.loads(open(change_path).read())
                except Exception:
                    pass

        # ── Active sprints ──
        from features.project.application.service import ProjectService

        active = ProjectService.find_active_sprint(project_id)
        info["active_sprint"] = active if active else None

        # ── CognitiveGraph schemas ──
        try:
            from libs.cognitive_graph import CognitiveGraph

            cg = CognitiveGraph(cog_data_dir, auto_register=True)
            info["cognitive_indices"] = cg.list_indexes()
        except Exception:
            pass

        return Response.ok(
            "project_info",
            message=f"Project '{project_id}' information",
            data=info,
            next_actions=[
                action(f"walker {project_id}/status", "see project health and workflow")
            ],
        )

    return _safe_execute(_exec)


def handle_list_projects(req: Request) -> Response:
    """Use case: list available projects."""

    def _exec():
        data = _list_from_global_registry()
        return Response.ok(
            "projects_list",
            message=f"{len(data.get('projects', []))} registered project(s)",
            data=data,
            next_actions=[
                action(
                    "walker <project>/status", "check a project health and workflow"
                ),
                action(
                    "walker prepare .", "register the current directory as a project"
                ),
            ],
        )

    return _safe_execute(_exec)


def handle_status(req: Request) -> Response:
    """Use case: show project health + workflow status.

    Thin wrapper over CheckHealthUseCase. Devuelve un Response REST
    con next_actions para que el agente sepa exactamente qué hacer.
    """

    def _exec():
        # If no project_id given, try to resolve from CWD or registry
        if not _resolve_project_from_cwd(req):
            # No project found: show list with two options
            return _list_from_global_registry_with_prompt()
        project_id = req.project_id
        repo_path = req.repo_path

        # If we have a project_id but no repo_path, resolve from global registry
        if not repo_path:
            repo_path = _resolve_repo_path_from_registry(project_id)
            if repo_path:
                req.set_repo_path(repo_path)

        health = CheckHealthUseCase.execute(
            project_id=project_id,
            repo_path=repo_path,
            sprint_id=req.sprint_id,
        )
        if req.flags.get("_legacy_warning"):
            health["warnings"].insert(0, req.flags["_legacy_warning"])

        # ── Response REST: message + next_actions (qué hacer ahora) ──
        sprint = health.get("sprint")
        if sprint:
            node = sprint.get("node", "?")
            sub = sprint.get("sub_state", "?")
            gates = sprint.get("gates", [])
            gate_names = ", ".join(g.get("name", "?") for g in gates) or "none"
            prompt = sprint.get("prompt", "")
            instructions = ""
            if prompt:
                # Resumir instrucciones accionables: primeras lineas no vacias
                lines = [l.strip() for l in prompt.split("\n") if l.strip()]
                instr = [
                    l for l in lines if not l.startswith("{") and not l.endswith("}")
                ]
                if instr:
                    instructions = " | ".join(instr[:3])
            message = (
                f"Project '{project_id}' at {node}.{sub} — "
                f"gates available: {gate_names}"
            )
            if sprint.get("execution_type") == "user_input":
                # User gate: la decision la toma el PO respondiendo (submit),
                # no con transition directa. Explicitar la via correcta.
                message = (
                    f"Project '{project_id}' at {node}.{sub} — "
                    f"awaiting your decision. Submit your response with the "
                    f"fields the prompt asks for (e.g. "
                    f'\'walker {project_id}/submit {{"decision":"...","evidence":"..."}}\'). '
                    f"Node gates ({gate_names}) are triggered by the submitted decision."
                )
            if instructions:
                message += f". {instructions}"
        else:
            message = f"Project '{project_id}' — no active sprint"

        actions = []
        for step in health.get("next_steps", []):
            cmd = step.get("command", "")
            if cmd:
                actions.append(action(cmd, step.get("reason", "")))
        # Fallback: sugerir consultar memorias si no hay next_steps
        if not actions:
            actions.append(
                action(
                    f"walker {project_id}/kg_schema",
                    "consult project memory indices before touching source code",
                )
            )

        return Response.ok(
            "project_health",
            message=message,
            data=health,
            next_actions=actions,
        )

    return _safe_execute(_exec)


def handle_prepare(req: Request) -> Response:
    """Use case: prepare project (walker prepare).

    Thin wrapper over PrepareProjectUseCase.
    """

    def _exec():
        project_path = os.path.abspath(
            req.project_id or req.flags.get("path", os.getcwd())
        )
        result = PrepareProjectUseCase.execute(
            project_path=project_path,
            args_flags=req.flags,
        )
        if result.get("error"):
            return Response.error(
                result["error"],
                status_code=400,
                next_actions=[
                    action(
                        "walker prepare <path>", "try again with a valid project path"
                    )
                ],
            )
        if result.get("type") == "already_exists":
            pid = result.get("project_id", "?")
            return Response.ok(
                "already_exists",
                message=result.get(
                    "message",
                    f"Project '{pid}' is already prepared. Initialization skipped.",
                ),
                data=result,
                next_actions=[
                    action(
                        f"walker {pid}/status",
                        "verify the existing project is healthy and scan memories",
                    )
                ],
            )
        pid = result.get("project_id", "?")
        result.get("next_steps", [])
        actions = [
            action(
                f"walker {pid}/status",
                "verify the project is healthy and scan memories",
            )
        ]
        return Response.ok(
            "prepare_result",
            message=f"Project '{pid}' prepared at {result.get('path', '?')}",
            data=result,
            next_actions=actions,
        )

    return _safe_execute(_exec)


def handle_submit(req: Request) -> Response:
    """Use case: submit data to current sub-state.

    REST: walker <project>/submit '{"field1":"value1","field2":"value2"}'
    Legacy: walker <project>/submit --field1 value1 --field2 value2
    """

    def _exec():
        if not _resolve_project_from_cwd(req):
            return _list_from_global_registry_with_prompt()
        chain_dir = _ensure_chain_dir(req.project_id, req.sprint_id)
        if not chain_dir:
            return Response.error("Could not resolve chain_dir", status_code=404)
        # Usar _payload (REST body) como datos a submitir
        payload = req.flags.get("_payload", {})
        submit_data = payload if isinstance(payload, dict) else req.flags
        api = WorkflowAPI(chain_dir=chain_dir, project_id=req.project_id)
        api.ensure_session()
        result = api.submit(**submit_data)

        # ── Response REST con next_actions ──
        if not result.get("ok", True) or result.get("error"):
            msg = result.get("error") or result.get("message", "Submit failed")
            return Response.error(
                msg,
                status_code=400,
                errors=result.get("errors", []),
                next_actions=[
                    action(
                        f"walker {req.project_id}/status",
                        "check which fields the current sub-state requires",
                    ),
                ],
            )
        sub_state = result.get("sub_state", "?")
        fields = list(submit_data.keys())
        message = f"Submitted {len(fields)} field(s) to {req.project_id} @ {sub_state}"
        # Teoria de grafos: si el submit resolvio un XOR-split por intent,
        # mostrar el avance automatico al nodo destino.
        if result.get("xor_done") and result.get("xor_next"):
            message += f" → avanzado a {result['xor_next']}"
        return Response.ok(
            "submit_result",
            message=message,
            data={"sub_state": sub_state, "field_count": len(fields), **result},
            next_actions=[
                action(
                    f"walker {req.project_id}/status",
                    "verify new workflow state and available gates",
                ),
                action(
                    f"walker {req.project_id}/transition <gate>",
                    "move to the next node if ready",
                ),
            ],
        )

    return _safe_execute(_exec)


def handle_transition(req: Request) -> Response:
    """Use case: transition between nodes."""

    def _exec():
        if not _resolve_project_from_cwd(req):
            return _list_from_global_registry_with_prompt()
        chain_dir = _ensure_chain_dir(req.project_id, req.sprint_id)
        if not chain_dir:
            return Response.error("Could not resolve chain_dir", status_code=404)
        gate = req.gate
        if not gate:
            return Response.error(
                "Missing gate — usage: walker transition <gate>",
                status_code=400,
                next_actions=[
                    action(f"walker {req.project_id}/status", "see the available gates")
                ],
            )
        api = WorkflowAPI(chain_dir=chain_dir, project_id=req.project_id)
        api.ensure_session()
        result = api.transition(gate)

        if not result.get("ok", True) or result.get("error"):
            return Response.error(
                result.get("error") or result.get("message", "Transition failed"),
                status_code=400,
                next_actions=[
                    action(
                        f"walker {req.project_id}/status",
                        "check current state and gates",
                    )
                ],
            )
        return Response.ok(
            "transition_result",
            message=(
                f"Transitioned {req.project_id} "
                f"{result.get('from_node', '?')}.{result.get('from_sub', '')} → "
                f"{result.get('to_node', '?')}.{result.get('to_sub', '')} "
                f"via '{gate}'"
            ),
            data={"gate": gate, **result},
            next_actions=[
                action(
                    f"walker {req.project_id}/status", "see the new state and its gates"
                ),
            ],
        )

    return _safe_execute(_exec)


def handle_task(req: Request) -> Response:
    """Use case: execute a registered task by name."""

    def _exec():
        if not _resolve_project_from_cwd(req):
            return _list_from_global_registry_with_prompt()
        chain_dir = _ensure_chain_dir(req.project_id, req.sprint_id)
        task_name = req.task_name or (req.positional[0] if req.positional else "")
        if not task_name:
            return Response.error(
                "Requires a task name — usage: walker <project>/<task_name>",
                status_code=400,
                next_actions=[action("walker help", "list all registered tasks")],
            )
        ctx = ExecutionContext.create(session_id="cli", chain_dir=chain_dir)
        # Pasar project_id a la task para que pueda resolver paths sin chain_dir
        task_data = dict(req.flags)
        task_data.setdefault("_project_id", req.project_id)
        # REST body (_payload): se mantiene como _payload (las tasks kg lo leen)
        # y ademas se desempaquetan sus keys (las tasks de negocio esperan el body directo).
        payload = task_data.get("_payload", {})
        if isinstance(payload, dict):
            for k, v in payload.items():
                task_data.setdefault(k, v)
        from features.engine.bootstrap import bootstrap

        registry, _ = bootstrap()

        # ── Regla de visibilidad: tareas internas del workflow no se invocan desde el CLI ──
        info = registry.get_task_info(task_name)
        if info and info.get("scope", "public") == "internal":
            return Response.error(
                f"Task '{task_name}' es INTERNA del workflow y no se invoca directamente. "
                f"El workflow la ejecuta al avanzar; usa 'walker {req.project_id}/status' "
                f"para ver el sub-estado y 'walker {req.project_id}/submit' o "
                f"'walker {req.project_id}/transition <gate>' para avanzar.",
                status_code=400,
                next_actions=[
                    action(
                        f"walker {req.project_id}/status",
                        "ver el sub-estado actual e instrucciones",
                    ),
                    action(
                        f"walker {req.project_id}/submit '{{\"...\"}}'",
                        "enviar los datos del nodo para avanzar",
                    ),
                ],
            )

        result = registry.execute(task_name, ctx, task_data)
        result_data = result.data or {}
        if not result.ok:
            result_data["error"] = result.error or result_data.get(
                "error", "Task failed"
            )
            return Response.error(
                result_data["error"],
                status_code=400,
                next_actions=[
                    action(
                        f"walker {req.project_id}/status", "check the project state"
                    ),
                ],
            )
        # Task exitosa: sugerir siguiente acción según el tipo de task
        next_actions = [
            action(f"walker {req.project_id}/status", "verify the new project state"),
        ]
        if task_name == "kg_schema":
            next_actions.insert(
                0,
                action(
                    f"walker {req.project_id}/kg_search",
                    "search the project memory with a text query",
                ),
            )
        elif task_name in (
            "scan_code_and_build_cpg",
            "build_initial_ckg",
            "kg_extract",
            "update_project",
        ):
            next_actions.insert(
                0,
                action(
                    f"walker {req.project_id}/status",
                    "confirm memories are up to date (FILES/CPG/CKG)",
                ),
            )
        return Response.ok(
            "task_result",
            message=f"Task '{task_name}' executed successfully",
            data={"action": task_name, "ok": True, "result": result_data},
            next_actions=next_actions,
        )

    return _safe_execute(_exec)


def handle_help(req: Request) -> Response:
    """Use case: show help with CognitiveGraph documentation."""

    def _exec():
        data = _build_help()
        return Response.ok(
            "help",
            message="Walker REST protocol — consume the walker as /recurso/recurso2",
            data=data,
            next_actions=[
                action("walker routes", "list all REST endpoints"),
                action("walker list", "see registered projects"),
            ],
        )

    return _safe_execute(_exec)


def _load_protocol_rest() -> str:
    """Carga el protocolo REST desde el template canónico (SSOT).

    SSOT: features/prompts/templates/protocol/rest.md (mismo root único de
    templates que el resto de prompts). Fallback: constante del código.
    """
    try:
        from features.prompts.loader import load_static
        text = load_static("protocol/rest")
        return text or _PROTOCOL_REST_FALLBACK
    except Exception:
        return _PROTOCOL_REST_FALLBACK


_PROTOCOL_REST_FALLBACK = (
    "PROTOCOLO REST OBLIGATORIO PARA EL AGENTE:\n"
    "El walker se consume como una ruta REST: walker /recurso/recurso2/recurso3\n"
    "Cada respuesta incluye 'Next Actions' con los comandos exactos para continuar.\n"
    "Para avanzar en el workflow SOLO hay dos comandos: submit y transition.\n"
    "  - walker <project>/submit '{\"campos del nodo\"}' → envia datos y el workflow avanza\n"
    "  - walker <project>/transition <gate> → avanza por una gate\n"
    "Las tareas del workflow (plan_sprint, spec_task, etc.) son INTERNAS: el workflow\n"
    "las ejecuta al recibir los datos. El agente no las llama directamente.\n"
    "Las tareas PUBLICAS (memorias kg_search/kg_schema/cpg/ckg) si se llaman directo:\n"
    "  walker <project>/kg_schema → descubre los indices disponibles\n"
    '  walker <project>/kg_search \'{"text":"..."}\' → busca en las memorias\n'
    "Antes de leer archivos del codigo fuente, consulta las memorias.\n"
)


def _build_help() -> dict:
    """Construye la ayuda completa: comandos fijos + tasks + indices discoverables.

    NO hardcodea tareas ni indices especificos. Todo se descubre dinamicamente:
    - TaskRegistry.discover() para tareas registradas
    - CognitiveGraph.schema() para indices y campos
    """
    # ── Tasks desde registry (dinamico, las tareas se auto-documentan) ──
    task_catalog = {"total": 0, "tasks": []}
    try:
        from features.engine.bootstrap import bootstrap

        registry, _ = bootstrap()
        task_catalog = registry.discover()
    except Exception:
        pass

    # ── Indices desde CognitiveGraph (dinamico, los indices se auto-documentan) ──
    indices_info = []
    try:
        from libs.cognitive_graph import CognitiveGraph

        cg = CognitiveGraph(".", auto_register=True)
        for name in sorted(cg.list_indexes()):
            try:
                schema = cg.schema(name)
                indices_info.append(
                    {
                        "name": name,
                        "description": schema.get("description", ""),
                        "fields": list(schema.get("fields", {}).keys()),
                        "types": list(schema.get("types", {}).keys()),
                        "total_docs": schema.get("total_docs", 0),
                    }
                )
            except Exception:
                indices_info.append({"name": name, "description": "", "fields": []})
    except Exception:
        pass

    # ── Mapa del workflow (dinamico, desde el grafo SSOT) ──
    workflow_map = {}
    try:
        from features.engine.application.workflow_map import compose_workflow_map

        workflow_map = compose_workflow_map()
    except Exception:
        workflow_map = {}

    return {
        "type": "help",
        "protocol": _load_protocol_rest(),
        "sprint_lifecycle": {
            "description": "El walker gestiona proyectos mediante sprints con un workflow determinista.",
            "como_crear_un_sprint": (
                "El agente NO llama las tareas del workflow directamente (son internas).\n"
                "El workflow ejecuta plan_sprint al recibir los datos del sub-estado:\n"
                "1. Opcional: prepara una lista de tareas como JSON array\n"
                "2. Usa 'walker <project>/status' para ver el sub-estado actual\n"
                '3. Envia los datos del nodo: walker <project>/submit \'{"goal":"Mi objetivo","tasks":[...]}\'\n'
                "4. El workflow ejecuta plan_sprint internamente y avanza al siguiente nodo"
            ),
            "nodos_del_workflow": (
                [
                    {
                        "nodo": n["id"],
                        "desc": n.get("description") or n.get("label", ""),
                        "sub_states": [ss["key"] for ss in n.get("sub_states", [])],
                    }
                    for n in workflow_map.get("nodes", [])
                ]
                if workflow_map.get("nodes")
                else [
                    {
                        "nodo": "SPRINT.PLANNING",
                        "desc": "Definir especificaciones del sprint",
                    },
                    {
                        "nodo": "SPRINT.ACTIVE",
                        "desc": "Sprint en ejecucion, avanzar tareas",
                    },
                    {"nodo": "SPRINT.REVIEW", "desc": "Revision del sprint"},
                    {"nodo": "SPRINT.CLOSED", "desc": "Sprint finalizado"},
                ]
            ),
            "transiciones": (
                "El workflow expone solo DOS comandos: submit y transition.\n"
                "  - submit: envia los datos del sub-estado actual (el workflow avanza solo)\n"
                "  - transition <gate>: avanza por una gate del workflow\n"
                "El agente NO necesita entender el workflow: es como un GPS, envia los datos\n"
                "correctos y avanza al siguiente paso. Ver gates disponibles con status."
            ),
        },
        "workflow_map": workflow_map,
        "commands": [
            {
                "name": "status",
                "desc": "Show workflow/project status with health checks",
                "usage": "walker <project>/status",
            },
            {
                "name": "submit",
                "desc": "Submit data to current sub-state (body JSON)",
                "usage": 'walker <project>/submit \'{"field":"value"}\'',
            },
            {
                "name": "transition",
                "desc": "Transition between nodes via gate",
                "usage": "walker <project>/transition <gate>",
            },
            {
                "name": "advance",
                "desc": "Advance to next sub-state",
                "usage": "walker <project>/advance",
            },
            {
                "name": "task",
                "desc": "Execute a registered PUBLIC task (memorias; las del workflow son internas)",
                "usage": "walker <project>/<task_name> [body JSON]",
            },
            {"name": "list", "desc": "List available projects", "usage": "walker list"},
            {
                "name": "prepare",
                "desc": "Init project at path",
                "usage": "walker prepare [path]",
            },
            {
                "name": "routes",
                "desc": "List all REST endpoints",
                "usage": "walker routes",
            },
            {
                "name": "mcp-config",
                "desc": "Genera la config MCP portable para usar las tools del walker en cualquier agente IA (Claude Code, Cursor, Cline, Codex, Roo...)",
                "usage": "walker mcp-config[?format=opencode|claude|cursor][&raw=true]",
            },
        ],
        "task_catalog": task_catalog,
        "indices": indices_info,
    }


def handle_mcp_config(req: Request) -> Response:
    """Use case: genera la configuración MCP portable para cualquier agente IA.

    Retorna la config MCP del walker en formato estándar (mcpServers) para
    que el usuario la use en cualquier agente (Claude Code, Cursor, Cline,
    Codex, Roo...) — no solo opencode.

    Flags (en req.params):
        format: standard | opencode | claude | cursor (default standard)
        raw: true → solo el bloque mcpServers
    """

    def _exec():
        from features.cli_interface.application.mcp_config_service import generate

        # Leer de flags (query params ?format=...) o params (path)
        fmt = req.flags.get("format") or req.params.get("format") or "standard"
        raw = (
            str(req.flags.get("raw") or req.params.get("raw") or "false").lower()
            == "true"
        )
        config = generate(fmt)
        data = config
        if raw:
            data = config.get("mcpServers", config)
        return Response.ok(
            "mcp_config",
            message=(
                "Configuración MCP del walker — lista para pegar en cualquier "
                "agente IA (Claude Code, Cursor, Cline, Codex, Roo, opencode...)"
            ),
            data=data,
            next_actions=[
                action("walker mcp-config --format=opencode", "config para opencode"),
                action(
                    "walker mcp-config --format=claude", "config para Claude Desktop"
                ),
                action("walker mcp-config --format=cursor", "config para Cursor"),
                action("walker routes", "list all REST endpoints"),
            ],
        )

    return _safe_execute(_exec)
