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

import logging
import os

from returns.pipeline import is_successful
from returns.result import safe

from features.project import ProjectService
from app.container import AppContainer
from shared.core.engine.domain.execution import ExecutionContext
from ..infrastructure.contracts import Request, Response, action


from .error_mapping import map_engine_error
from . import presenter

logger = logging.getLogger(__name__)


def _safe_execute(fn, *args, **kwargs) -> Response:
    """Execute fn and capture errors into a Response estándar.

    SSOT for error handling across all handlers.
    Cada error se traduce a un Response REST con status_code HTTP:
      404 → recurso no encontrado, 400 → validación, 500 → interno.
    Los WorkflowEngineError se mapean con la taxonomía del dominio
    (patrón Result) preservando details/errors para el agente.
    """
    outcome = _execute_core(fn, *args, **kwargs)
    if is_successful(outcome):
        return outcome.value_or(Response.error("Empty result", status_code=500))
    error = outcome.failure()
    if isinstance(error, FileNotFoundError):
        return Response.error(f"File not found: {error}", status_code=404)
    if isinstance(error, ValueError):
        return Response.error(f"Validation error: {error}", status_code=400)
    payload = map_engine_error(error)
    return Response.error(
        payload["message"],
        status_code=payload["status_code"],
        errors=payload["errors"],
    )


@safe
def _execute_core(fn, *args, **kwargs) -> Response:
    """Core puro de _safe_execute (puede lanzar)."""
    return fn(*args, **kwargs)


def _record_forced_transition(
    req: Request, gate: str, reason: str, result: dict, module_registry=None
) -> None:
    """Registra en trace una transición forzada (GAP-4).

    Analogía git --force-with-lease: la fuerza de precondiciones siempre
    queda documentada y recuperable. Usa patrón Result (returns): si falla,
    se loggea el Failure, nunca rompe el flujo.

    El tracer se resuelve por id+contrato desde el module_registry del
    composition root (ya construido en el flujo del handler, ADR-WALKER-062 /
    F3) — no se construye el concreto TraceLogger aquí.
    """
    from returns.result import safe
    from returns.pipeline import is_successful

    @safe
    def _trace() -> bool:
        # ADR-WALKER-062 (consistencia F3): resolver el tracer por id+contrato
        # desde el module_registry del composition root, no construir el concreto.
        tracer = None
        if module_registry is not None:
            try:
                tracer = module_registry.get_service("tracer")
            except Exception:
                tracer = None
        if tracer is not None:
            tracer.trace(
                req.flags.get("_chain_dir", ""),
                "WORKFLOW_FORCED",
                data={
                    "gate": gate,
                    "reason": reason,
                    "from": f"{result.get('from_node', '?')}.{result.get('from_sub', '')}",
                    "to": f"{result.get('to_node', '?')}.{result.get('to_sub', '')}",
                },
            )
        return True

    outcome = _trace()
    if not is_successful(outcome):
        import logging

        logging.getLogger(__name__).warning(
            "No se pudo trazar transición forzada (best-effort): %s — %s",
            gate,
            outcome.failure(),
        )


def _sync_watcher(project_id: str) -> None:
    """Escanea cambios en el proyecto. Como git: siempre actualizado antes de operar.

    Se ejecuta al inicio de cada comando que opera sobre un proyecto,
    manteniendo el FileWatcher sincronizado sin necesidad de daemon.

    Best-effort: si el watcher falla, loguea el error a stderr — nunca
    rompe el flujo del llamador (return pattern: el error es observable).
    """
    if not project_id:
        return
    outcome = _sync_watcher_core(project_id)
    if is_successful(outcome):
        return
    error = outcome.failure()
    logger.warning(
        "watcher sync failed for '%s' (%s: %s)",
        project_id,
        type(error).__name__,
        error,
    )


@safe
def _sync_watcher_core(project_id: str) -> None:
    """Core puro de _sync_watcher (puede lanzar)."""
    repo_path = _resolve_repo_path_from_registry(project_id)
    if repo_path and os.path.isdir(repo_path):
        from features.project import FileWatcher
        from app.path_resolver import get_data_dir
        from features.code_analysis.infrastructure.repository import (
            make_cpg_fingerprint_reader,
        )

        # data_dir canónico = .cognitive/projects/<pid>/data (fuera del árbol
        # escaneado). Si se usa el default (<repo>/data), change_detected.json
        # y .file_snapshot.json se escriben DENTRO del scope del watcher →
        # auto-invalidación en cada scan (fingerprint nunca coincide con el
        # CPG → stale perpetuo). Scope = repo_path completo (ADR-WALKER-054 D1).
        watcher = FileWatcher(
            repo_path,
            data_dir=str(get_data_dir(project_id)),
            cpg_fingerprint_reader=make_cpg_fingerprint_reader(
                str(get_data_dir(project_id))
            ),
        )
        watcher.scan()


def _ensure_chain_dir(project_id: str, sprint_id: str = "") -> str:
    """Resolve chain_dir. SSOT: delegates to ProjectService.

    Antes de resolver, sincroniza el watcher (como git status).
    """
    _sync_watcher(project_id)
    if not project_id:
        return ""
    if sprint_id:
        from app.path_resolver import get_sprint_dir

        return str(get_sprint_dir(project_id, sprint_id))
    return ProjectService.find_active_sprint(project_id) or ""


def _list_from_global_registry() -> dict:
    """Read projects from global registry data/projects.json.

    Return-pattern: devuelve (projects, error) embebido en el dict —
    nunca traga el fallo en silencio.
    """
    from app.path_resolver import get_walker_root

    registry_path = get_walker_root() / "data" / "projects.json"
    if registry_path.exists():
        outcome = _read_registry_core(registry_path)
        if is_successful(outcome):
            registry = outcome.value_or({})
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
        else:
            error = outcome.failure()
            return {"type": "projects_list", "projects": [], "error": str(error)}
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
            presenter.go_list(),
            presenter.go_prepare(),
        ],
    )


def _resolve_repo_path_from_registry(project_id: str) -> str:
    """Look up a project's repo_path from the global registry."""
    from app.path_resolver import get_walker_root

    registry_path = get_walker_root() / "data" / "projects.json"
    if registry_path.exists():
        outcome = _read_registry_core(registry_path)
        if is_successful(outcome):
            projects = outcome.value_or({}).get("projects", {})
            if project_id in projects:
                return projects[project_id].get("repo_path", "")
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
    return True


# ── Cores @safe (IO) ────────────────────────────────────────────────


@safe
def _read_registry_core(registry_path) -> dict:
    """Core puro: lee y parsea un JSON (puede lanzar)."""
    import json

    with open(registry_path) as f:
        return json.load(f)


@safe
def _memory_stats_core(cog_data_dir: str) -> dict:
    """Core puro: stats de memoria + indices (puede lanzar)."""
    from app.memories import MemoryQueryService
    from app.memories.registration import apply_memory_indexes

    # ADR-068 Fase 2: índices auto-registrados por las features (forFeature);
    # apply_memory_indexes es idempotente (dedupe en collector).
    mqs = MemoryQueryService(cog_data_dir, register_indexes=apply_memory_indexes)
    return {
        "status": mqs.status(),
        "indexes": mqs.list_indexes(),
    }


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
            "warnings": [],
        }

        if not repo_path:
            return Response.error(
                f"Project '{project_id}' not found in registry",
                status_code=404,
                next_actions=[presenter.go_list()],
            )

        import os

        info["repo_path"] = repo_path

        # ── Registry data ──
        from app.path_resolver import get_walker_root

        registry_path = get_walker_root() / "data" / "projects.json"
        if registry_path.exists():
            reg_outcome = _read_registry_core(registry_path)
            if is_successful(reg_outcome):
                reg = reg_outcome.value_or({}).get("projects", {}).get(project_id, {})
                info["name"] = reg.get("name", project_id)
                info["registered_at"] = reg.get("registered_at", "")
                info["last_accessed"] = reg.get("last_accessed", "")
            else:
                error = reg_outcome.failure()
                info["warnings"].append(f"registry read failed: {error}")

        # ── Project metadata ──
        cog_data_dir = os.path.join(
            repo_path, ".cognitive", "projects", project_id, "data"
        )
        proj_json = os.path.join(cog_data_dir, "project.json")
        if not os.path.exists(proj_json):
            proj_json = os.path.join(repo_path, "project.json")
        if os.path.exists(proj_json):
            proj_outcome = _read_registry_core(proj_json)
            if is_successful(proj_outcome):
                pdata = proj_outcome.value_or({})
                proj = pdata.get("project", pdata)
                identity = proj.get("identity", {})
                info["language"] = identity.get("language", "")
                info["framework"] = identity.get("framework", "")
            else:
                error = proj_outcome.failure()
                info["warnings"].append(f"project.json read failed: {error}")

        # ── Memory stats via MemoryQueryService (no acceso directo a JSON) ──
        if os.path.isdir(cog_data_dir):
            mem_outcome = _memory_stats_core(cog_data_dir)
            if is_successful(mem_outcome):
                mem = mem_outcome.value_or({})
                info["cpg_nodes"] = (
                    mem.get("status", {}).get("cpg", {}).get("total_docs", 0)
                )
                info["cpg_edges"] = (
                    mem.get("status", {}).get("cpg", {}).get("total_edges", 0)
                )
                info["ckg_docs"] = (
                    mem.get("status", {}).get("ckg", {}).get("total_docs", 0)
                )
                dt = mem.get("status", {}).get("delta", {})
                info["delta_tasks"] = dt.get("total_docs", 0)
                info["cognitive_indices"] = mem.get("indexes", [])
            else:
                error = mem_outcome.failure()
                info["warnings"].append(f"memory stats failed: {error}")

        # Watcher: change_detected
        change_path = os.path.join(cog_data_dir, "change_detected.json")
        if os.path.exists(change_path):
            watcher_outcome = _read_registry_core(change_path)
            if is_successful(watcher_outcome):
                info["watcher"] = watcher_outcome.value_or({})
            else:
                error = watcher_outcome.failure()
                info["warnings"].append(f"watcher file read failed: {error}")

        # Active sprints
        from features.project.application.service import ProjectService

        active = ProjectService.find_active_sprint(project_id)
        info["active_sprint"] = active if active else None

        return Response.ok(
            "project_info",
            message=f"Project '{project_id}' information",
            data=info,
            next_actions=presenter.project_actions(project_id),
        )

    return _safe_execute(_exec)


def handle_list_projects(req: Request) -> Response:
    """Use case: list available projects.

    Lee el registro global directamente (_list_from_global_registry) en vez
    de rondar por el tool MCP interno: el CLI es solo un formateador.
    (El circuito CLI->MCP->REST envolvia el payload bajo "data", por lo que
    la salida humana mostraba "0 registered projects" aunque el registro
    tuviera proyectos.)
    """

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

    Resuelve CheckHealthUseCase via get_module_service("workflow") (composition
    root único, capa_rout.md D1) — el CLI es solo formateador.
    NOTA: el MCP es el SSOT para el agente (LLM). El CLI no debe consumir
    el tool MCP 'status' internamente: el circuito CLI->MCP->REST->CLI
    recaia en si mismo (recursion hasta "maximum recursion depth exceeded")
    y ademas envolvia el payload bajo 'data', dejando la salida en '?'.
    """

    def _exec():
        if not _resolve_project_from_cwd(req):
            return _list_from_global_registry_with_prompt()
        project_id = req.project_id
        repo_path = req.repo_path
        if not repo_path:
            repo_path = _resolve_repo_path_from_registry(project_id)
            if repo_path:
                req.set_repo_path(repo_path)

        from app.app import WalkerApp

        app = WalkerApp(AppContainer.build())
        health = app.status(
            project_id=project_id,
            repo_path=repo_path,
        )

        sprint = health.get("sprint")
        if sprint:
            node = sprint.get("node", "?")
            sub = sprint.get("sub_state", "?")
            gates = sprint.get("gates", [])
            gate_names = ", ".join(g.get("name", "?") for g in gates) or "none"
            prompt = sprint.get("prompt", "")
            instructions = ""
            if prompt:
                lines = [l.strip() for l in prompt.split("\n") if l.strip()]
                instr = [
                    l for l in lines if not l.startswith("{") and not l.endswith("}")
                ]
                if instr:
                    instructions = " | ".join(instr[:3])
            if sprint.get("informal") and not node:
                sprint_id = sprint.get("sprint_id") or "?"
                message = (
                    f"Project '{project_id}' — sprint informal '{sprint_id}' "
                    f"(session log de captura). No tiene estado de workflow; "
                    f"consulta su historico con "
                    f"walker {project_id}/recall_knowledge "
                    f'\'{{"_index":"delta","text":"{sprint_id}"}}\''
                )
            else:
                message = (
                    f"Project '{project_id}' at {node}.{sub} — "
                    f"gates available: {gate_names}"
                )
                if sprint.get("execution_type") == "user_input":
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
        if not actions:
            actions.append(
                action(
                    f"walker {project_id}/memory_schema",
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

    Resuelve PrepareProjectUseCase via get_module_service("workflow")
    (composition root único, capa_rout.md D1) — el CLI es solo formateador.
    El antiguo circuito CLI->MCP->REST envolvia el payload del use case bajo
    'data' del Response, y prepare_output leía claves planas -> todo '?'
    aunque el manifest se hubiera creado correctamente.
    """

    def _exec():
        project_path = os.path.abspath(
            req.project_id or req.flags.get("path", os.getcwd())
        )

        from app.app import WalkerApp

        app = WalkerApp(AppContainer.build())
        result = app.prepare(
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

    Llama a SubmitDataUseCase directamente (el CLI es solo formateador).
    NOTA: el MCP es el SSOT para el agente (LLM). El CLI no debe consumir
    el tool MCP 'submit' internamente: el circuito CLI->MCP->REST->CLI
    recaia en si mismo (recursion hasta "maximum recursion depth exceeded")
    y ademas envolvía el payload bajo 'data', dejando la salida en '?'.
    """

    def _exec():
        if not _resolve_project_from_cwd(req):
            return _list_from_global_registry_with_prompt()
        project_id = req.project_id
        payload = req.flags.get("_payload", {})
        submit_data = payload if isinstance(payload, dict) else req.flags

        from app.container import AppContainer

        AppContainer.build()

        chain_dir = (
            req.params.get("chain_dir", "")
            or req.flags.get("_chain_dir", "")
            or _ensure_chain_dir(project_id)
        )
        if not chain_dir:
            return Response.error(
                f"No active sprint found for project '{project_id}'",
                status_code=404,
                next_actions=[
                    action(
                        f"walker {project_id}/status",
                        "check project health and active sprints",
                    )
                ],
            )

        from app.app import WalkerApp

        app = WalkerApp(AppContainer.build())
        result = app.submit(chain_dir, fields=submit_data)

        sub_state = result.get("sub_state", "?")
        fields = list(submit_data.keys())
        message = f"Submitted {len(fields)} field(s) to {project_id} @ {sub_state}"
        xor_next = result.get("xor_next")
        if xor_next:
            message += f" → avanzado a {xor_next}"
        return Response.ok(
            "submit_result",
            message=message,
            data={"sub_state": sub_state, "field_count": len(fields), **result},
            next_actions=[
                action(
                    f"walker {project_id}/status",
                    "verify new workflow state and available gates",
                ),
                action(
                    f"walker {project_id}/transition <gate>",
                    "move to the next node if ready",
                ),
            ],
        )

    return _safe_execute(_exec)


def handle_transition(req: Request) -> Response:
    """Use case: transition between nodes.

    Llama a TransitionUseCase directamente (el CLI es solo formateador).
    NOTA: el MCP es el SSOT para el agente (LLM). El CLI no debe consumir
    el tool MCP 'transition' internamente: el circuito CLI->MCP->REST->CLI
    recaia en si mismo (recursion hasta "maximum recursion depth exceeded")
    y ademas envolvia el payload bajo 'data', dejando la salida en '?'.
    """

    def _exec():
        if not _resolve_project_from_cwd(req):
            return _list_from_global_registry_with_prompt()
        project_id = req.project_id
        gate = req.gate
        if not gate:
            return Response.error(
                "Missing gate — usage: walker transition <gate>",
                status_code=400,
                next_actions=[presenter.go_status(req.project_id)],
            )
        force = bool(req.flags.get("force") or req.flags.get("f"))
        force_reason = req.flags.get("reason", "") or req.flags.get("force_reason", "")
        if force and not force_reason:
            return Response.error(
                "Force transition requires --reason (documenta "
                "la decisión). Ej: walker transition <gate> --force --reason '...'",
                status_code=400,
            )

        from app.container import AppContainer

        AppContainer.build()

        chain_dir = (
            req.params.get("chain_dir", "")
            or req.flags.get("_chain_dir", "")
            or _ensure_chain_dir(project_id)
        )
        if not chain_dir:
            return Response.error(
                f"No active sprint found for project '{project_id}'",
                status_code=404,
                next_actions=[
                    action(
                        f"walker {project_id}/status",
                        "check project health and active sprints",
                    )
                ],
            )

        from app.app import WalkerApp

        container = AppContainer.build()
        app = WalkerApp(container)
        result = app.transition(
            chain_dir,
            gate_name=gate,
            skip_preconditions=force,
            reason=force_reason,
        )

        if force:
            _record_forced_transition(req, gate, force_reason, result, container.module_registry)
        return Response.ok(
            "transition_result",
            message=(
                f"Transitioned {project_id} "
                f"{result.get('from_node', '?')} → "
                f"{result.get('to_node', '?')} "
                f"via '{gate}'"
            ),
            data={"gate": gate, **result},
            next_actions=[
                action(
                    f"walker {project_id}/status",
                    "verify new workflow state",
                ),
            ],
        )

    return _safe_execute(_exec)


def handle_task(req: Request) -> Response:
    """Use case: execute a registered task by name."""

    def _exec():
        if not _resolve_project_from_cwd(req):
            return _list_from_global_registry_with_prompt()

        # ── Composition root: construir el contenedor y OBTENER el
        #    task_registry para inyectarlo al dispatcher. Los otros handlers
        #    lo hacen via WalkerApp(AppContainer.build()); handle_task solo
        #    construía el contenedor sin pasarlo → ADR-WALKER-059: el
        #    dispatcher exige registry inyectado (sin service locator). ──
        _app = AppContainer.build()

        chain_dir = _ensure_chain_dir(req.project_id, req.sprint_id)
        task_name = req.task_name or (req.positional[0] if req.positional else "")
        if not task_name:
            return Response.error(
                "Requires a task name — usage: walker <project>/<task_name>",
                status_code=400,
                next_actions=[presenter.go_help()],
            )
        # Pasar project_id a la task para que pueda resolver paths sin chain_dir
        task_data = dict(req.flags)
        task_data.setdefault("_project_id", req.project_id)
        # REST body (_payload): se mantiene como _payload (las tasks kg lo leen)
        # y ademas se desempaquetan sus keys (las tasks de negocio esperan el body directo).
        payload = task_data.get("_payload", {})
        if isinstance(payload, dict):
            for k, v in payload.items():
                task_data.setdefault(k, v)

        # ── ADR-WALKER-053: contexto de proyecto resuelto UNA vez aquí.
        # Precedencia explícito > ambiental: payload.project_id/_project_id
        # gana sobre req.project_id del route. Viaja como _project_ctx;
        # ctx.chain_dir conserva su semántica legacy (sprint dir). ──
        try:
            from tasks.application.project_context import (
                resolve_project_context,
            )

            _pctx = resolve_project_context(
                payload_pid=(
                    payload.get("project_id") or payload.get("_project_id") or ""
                )
                if isinstance(payload, dict)
                else "",
                route_pid=req.project_id,
            )
            if _pctx.resolved:
                task_data["_project_ctx"] = dict(
                    project_id=_pctx.project_id,
                    repo_path=_pctx.repo_path,
                    sprint_chain_dir=_pctx.sprint_chain_dir,
                )
        except Exception as exc:
            # Best-effort: los guards legacy siguen operando, pero VISIBLE
            # (convention-001: nunca silencioso).
            logger.warning(
                "_project_ctx no se pudo resolver (%s: %s)", type(exc).__name__, exc
            )

        # ── Dispatch único (SPEC-005 #5): mismo core que el MCP. La visibilidad
        #    interna vive SOLO en SystemTaskRegistry (metadata.scope SSOT). ──
        from shared.core.engine.application.task_dispatcher import execute_public_task

        # Canal de invocación (SPEC-002 G-W1): el MCP adapter inyecta
        # _channel="mcp" en los flags (Camino A); el CLI no lo pasa → "cli".
        # ANTES el MCP despachaba por el Router REST con channel="cli" y las
        # tools con route_def (recall_knowledge, documenting_knowledge, ...) trazaban el canal
        # equivocado en el TraceGraph.
        channel = req.flags.get("_channel", "cli")
        outcome = execute_public_task(
            task_name,
            req.project_id,
            chain_dir,
            task_data,
            session_id="cli",
            channel=channel,
            registry=_app.module_registry.get_service("task_registry"),
        )
        if not outcome.get("ok"):
            if outcome.get("blocked"):
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
            return Response.error(
                outcome.get("error", "Task failed"),
                status_code=400,
                next_actions=[
                    action(
                        f"walker {req.project_id}/status", "check the project state"
                    ),
                ],
            )
        result_data = outcome.get("data", {})
        # Task exitosa: sugerir siguiente acción según el tipo de task
        next_actions = [
            presenter.go_status(req.project_id),
        ]
        if task_name == "memory_schema":
            next_actions.insert(
                0,
                action(
                    f"walker {req.project_id}/recall_knowledge",
                    "search the project memory with a text query",
                ),
            )
        elif task_name in (
            "scan_code_and_build_cpg",
            "build_initial_ckg",
            "extract_knowledge",
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
        from protocols.cli.cli_interface.application.mcp_client_holder import get_mcp_client

        mcp = get_mcp_client()
        mcp_tools = mcp.list_tools()
        data = _build_help()
        data["mcp_tools_count"] = len(mcp_tools)
        data["mcp_tools"] = [t.get("name") for t in mcp_tools[:10]]
        return Response.ok(
            "help",
            message="Walker REST protocol — consume the walker as /recurso/recurso2",
            data=data,
            next_actions=[
                presenter.go_routes(),
                presenter.go_list(),
            ],
        )

    return _safe_execute(_exec)


def handle_mcp_tools(req: Request) -> Response:
    """Use case: list all MCP tools from ModuleRegistry via MCP client."""

    def _exec():
        from protocols.cli.cli_interface.application.mcp_client_holder import get_mcp_client

        mcp = get_mcp_client()
        tools = mcp.list_tools()
        return Response.ok(
            "mcp_tools",
            message=f"{len(tools)} MCP tool(s) available",
            data={"tools": tools},
            next_actions=[
                action("walker help", "see full help"),
                action("walker routes", "see REST routes"),
            ],
        )

    return _safe_execute(_exec)


def handle_mcp_routes(req: Request) -> Response:
    """Use case: list all REST routes from ModuleRegistry."""

    def _exec():
        from protocols.cli.cli_interface.application.mcp_client_holder import get_mcp_client

        mcp = get_mcp_client()
        routes = mcp.list_tools()
        return Response.ok(
            "mcp_routes",
            message=f"{len(routes)} REST route(s) available",
            data={"routes": routes},
            next_actions=[
                action("walker help", "see full help"),
                action("walker mcp-tools", "see MCP tools"),
            ],
        )

    return _safe_execute(_exec)


def _load_protocol_rest() -> str:
    """Carga el protocolo REST desde el template canónico (SSOT).

    SSOT: features/prompts/templates/protocol/rest.md (mismo root único de
    templates que el resto de prompts). Fallback: constante del código.
    """
    outcome = _load_protocol_rest_core()
    if is_successful(outcome):
        return outcome.value_or(_PROTOCOL_REST_FALLBACK) or _PROTOCOL_REST_FALLBACK
    error = outcome.failure()
    logger.warning(
        "protocol/rest load failed (%s: %s); usando fallback",
        type(error).__name__,
        error,
    )
    return _PROTOCOL_REST_FALLBACK


@safe
def _load_protocol_rest_core() -> str:
    """Core puro de _load_protocol_rest (puede lanzar)."""
    from features.workflow.application.prompts.loader import load_static

    return load_static("protocol/rest")


_PROTOCOL_REST_FALLBACK = (
    "PROTOCOLO REST OBLIGATORIO PARA EL AGENTE:\n"
    "El walker se consume como una ruta REST: walker /recurso/recurso2/recurso3\n"
    "Cada respuesta incluye 'Next Actions' con los comandos exactos para continuar.\n"
    "Para avanzar en el workflow SOLO hay dos comandos: submit y transition.\n"
    "  - walker <project>/submit '{\"campos del nodo\"}' → envia datos y el workflow avanza\n"
    "  - walker <project>/transition <gate> → avanza por una gate\n"
    "Las tareas del workflow (plan_sprint, spec_task, etc.) son INTERNAS: el workflow\n"
    "las ejecuta al recibir los datos. El agente no las llama directamente.\n"
    "Las tareas PUBLICAS (memorias recall_knowledge/memory_schema/cpg/ckg) si se llaman directo:\n"
    "  walker <project>/memory_schema → descubre los indices disponibles\n"
    '  walker <project>/recall_knowledge \'{"_index":"ckg","text":"..."}\' → busca en las memorias\n'
    "Antes de leer archivos del codigo fuente, consulta las memorias.\n"
)


def _build_help() -> dict:
    """Construye la ayuda completa: comandos fijos + tasks + indices discoverables.

    NO hardcodea tareas ni indices especificos. Todo se descubre dinamicamente:
    - TaskRegistry.discover() para tareas registradas
    - CognitiveGraph.schema() para indices y campos
    """
    # ── Tasks desde registry (dinamico, las tareas se auto-documentan) ──
    task_catalog = {"total": 0, "tasks": [], "errors": []}
    catalog_outcome = _discover_task_catalog_core()
    if is_successful(catalog_outcome):
        task_catalog = catalog_outcome.value_or(task_catalog)
    else:
        error = catalog_outcome.failure()
        task_catalog["errors"].append(f"task registry discover failed: {error}")

    # ── Indices desde CognitiveGraph (dinamico, los indices se auto-documentan) ──
    indices_info = []
    index_errors: list[str] = []
    cg_outcome = _open_cognitive_graph_core()
    if is_successful(cg_outcome):
        cg = cg_outcome.value_or(None)
        if cg is not None:
            for name in sorted(cg.list_indexes()):
                schema_outcome = _index_schema_core(cg, name)
                if is_successful(schema_outcome):
                    schema = schema_outcome.value_or({})
                    indices_info.append(
                        {
                            "name": name,
                            "description": schema.get("description", ""),
                            "fields": list(schema.get("fields", {}).keys()),
                            "types": list(schema.get("types", {}).keys()),
                            "total_docs": schema.get("total_docs", 0),
                        }
                    )
                else:
                    error = schema_outcome.failure()
                    index_errors.append(f"schema '{name}' failed: {error}")
                    indices_info.append({"name": name, "description": "", "fields": []})
    else:
        error = cg_outcome.failure()
        index_errors.append(f"cognitive graph open failed: {error}")

    # ── Mapa del workflow (dinamico, desde el grafo SSOT) ──
    workflow_map = {}
    wf_outcome = _compose_workflow_map_core()
    if is_successful(wf_outcome):
        workflow_map = wf_outcome.value_or({})
    else:
        error = wf_outcome.failure()
        logger.warning(
            "workflow map compose failed (%s: %s)",
            type(error).__name__,
            error,
        )
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
        "index_errors": index_errors,
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
        from protocols.cli.cli_interface.application.mcp_config_service import generate

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
                presenter.go_mcp_config("opencode"),
                action(
                    "walker mcp-config --format=claude", "config para Claude Desktop"
                ),
                presenter.go_mcp_config("cursor"),
                presenter.go_routes(),
            ],
        )

    return _safe_execute(_exec)


@safe
def _discover_task_catalog_core() -> dict:
    """Core puro: descubre el task registry (puede lanzar)."""
    from app.container import AppContainer

    app = AppContainer.build()
    return app.task_registry.discover()


@safe
def _open_cognitive_graph_core():
    """Core puro: abre CognitiveGraph (puede lanzar).

    Resuelve la raíz cognitiva CANÓNICA del proyecto (por manifest
    .cognitive/projects/<pid>/data/project.json) en vez de usar "." — si se
    abre con CWD (raíz del repo), cualquier store() escribiria las memorias
    en <repo>/data en vez de .cognitive/projects/<pid>/data.
    """
    from app.composition.registry import _build_graph

    # Result pattern: el core @safe nunca lanza; ante fallo de resolución se
    # degrada a "." (CWD) sin except.
    root = _resolve_cognitive_root_core().value_or(".")
    return _build_graph(root)


@safe
def _resolve_cognitive_root_core() -> str:
    """Core puro: resuelve la raíz cognitiva canónica del proyecto (puede lanzar)."""
    from app.path_resolver import get_cognitive_dir, resolve_project_root

    resolved = resolve_project_root(".")
    if resolved:
        return resolved
    return str(get_cognitive_dir())


@safe
def _index_schema_core(cg, name: str) -> dict:
    """Core puro: schema de un índice (puede lanzar)."""
    return cg.schema(name)


@safe
def _compose_workflow_map_core() -> dict:
    """Core puro: compone el mapa del workflow (puede lanzar)."""
    from shared.core.engine.application.workflow_map import compose_workflow_map
    from shared.core.engine.infrastructure.repositories import WorkflowRepository

    return compose_workflow_map(workflow_repo=WorkflowRepository())
