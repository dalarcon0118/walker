"""fast_mcp.composition — El ÚNICO punto de cableado entre contexts (§3bis).

Cada context expone su proveedor; este módulo los construye e inyecta por
constructor. La superficie (phases/) solo consume objetos ya cableados.
Regla de coherencia 1: aquí SÍ se cruzan contexts; en ningún otro módulo.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fast_mcp.features.project.application.use_cases import ProjectCtx
from fast_mcp.features.project.infrastructure.layout import ProjectLayout
from fast_mcp.shared_kernel.registry_store import RegistryStore

logger = logging.getLogger(__name__)


# ── Handler registration (composition root — R1/§3bis) ────────────────
# Delta + trace handler registries are populated HERE, at import time.
# This is the ONLY place cross-feature wiring is legal.

def _register_delta_handlers():
    """Register delta_tracking handlers into the dispatch registry."""
    from fast_mcp.features.delta_tracking.handlers import register_handler
    from fast_mcp.features.delta_tracking.handlers.sprint import (
        handle_sprint_planning,
        handle_resolve_project_sprint,
    )
    register_handler("sprint_planning", handle_sprint_planning)
    register_handler("resolve_project_sprint", handle_resolve_project_sprint)
    logger.debug("composition: delta handlers registered")


def _register_trace_handlers():
    """Register event_tracing handlers into the dispatch registry."""
    from fast_mcp.features.event_tracing.handlers import register_trace_handlers
    from fast_mcp.features.event_tracing.handlers.trace import (
        handle_trace_message,
        handle_trace_push,
    )
    register_trace_handlers({
        "trace_push": handle_trace_push,
        "trace_message": handle_trace_message,
    })
    logger.debug("composition: trace handlers registered")


# Run registration at import time (composition root)
_register_delta_handlers()
_register_trace_handlers()


# ── Session-level tool runners (no project context required) ─────────

def session_tool_runner(name: str, root: Path | None = None):
    """Runner for tools that work without project context (help, list_projects)."""

    def runner(**kwargs) -> dict:
        if name == "help":
            return _handle_help(kwargs)
        if name == "list_projects":
            return _handle_list_projects(kwargs)
        if name == "project_for_path":
            return _handle_project_for_path(kwargs)
        return {"error": f"Unknown session tool: {name}"}

    return runner


def _handle_help(kwargs: dict) -> dict:
    """List available tools (no project context needed)."""
    tool_filter = kwargs.get("tool", "")
    import os
    import sys as _sys
    _system_site = "/Library/Frameworks/Python.framework/Versions/3.12/lib/python3.12/site-packages"
    if _system_site not in _sys.path and os.path.isdir(_system_site):
        _sys.path.insert(0, _system_site)
    try:
        from fast_mcp.server import build_server
        server = build_server()
        tools = []
        for tool_name in sorted(server._tool_manager._tools.keys()):
            if tool_filter and tool_filter != tool_name:
                continue
            tool_obj = server._tool_manager._tools[tool_name]
            tools.append({
                "name": tool_name,
                "description": getattr(tool_obj, "description", ""),
            })
        return {"tools": tools, "count": len(tools)}
    except Exception as e:  # noqa: BLE001
        return {
            "error": f"Could not list tools: {e}",
            "hint": (
                "El paquete 'mcp' no esta disponible en este entorno. "
                "Usa el Python del sistema o instala 'mcp' en el venv: pip install mcp. "
                "Alternativa: ./bin/walker3 walker/cockpit '{...}' no requiere build_server."
            ),
        }


def _handle_project_for_path(kwargs: dict) -> dict:
    """Resuelve {project_id, repo_path} desde un path (git-like).

    Lo usa el plugin global para saber en qué proyecto está corriendo la sesión;
    sin esto tendría que adivinar por nombre de directorio.
    """
    from fast_mcp.shared_kernel.project_support import ProjectService

    path = kwargs.get("path") or kwargs.get("directory") or "."
    try:
        found = ProjectService.resolve_from_registry(str(path))
    except Exception as exc:  # noqa: BLE001 — se reporta, no se rompe
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if not found:
        return {"ok": False, "error": f"sin proyecto registrado para {path}"}
    return {"ok": True, "project_id": found.get("project_id", ""),
            "repo_path": found.get("repo_path", "")}


def _handle_list_projects(kwargs: dict) -> dict:
    """List registered projects (no project context needed)."""
    try:
        from fast_mcp.shared_kernel.registry_store import RegistryStore
        store = RegistryStore()
        data = store.read()
        projects_dict = data.get("projects", {})
        return {
            "ok": True,
            "projects": list(projects_dict.keys()),
            "count": len(projects_dict),
        }
    except Exception as e:
        return {"ok": False, "error": f"Could not list projects: {e}"}


@dataclass(frozen=True)
class Contexts:
    """Contextos del server (se extiende por fase: memories, workflow...)."""

    project: ProjectCtx


def build(root: Path | None = None) -> Contexts:
    """Cablea los contexts. `root` inyectable para tests (raíz propia)."""
    store = RegistryStore(root)
    project = ProjectCtx(store=store, layout=ProjectLayout(store, root))
    return Contexts(project=project)


def project_tool_runner(name: str, root: Path | None = None):
    """Runner de tool del context project, con contexto YA cableado.

    Es lo que la superficie (phases/) registra en FastMCP: phases nunca
    importa features — recibe el callable cerrado sobre su contexto
    (regla de coherencia §3bis 2 y 3).
    """
    from fast_mcp.features.project.application import use_cases

    ctx = build(root).project

    def runner(**kwargs) -> dict:
        if name == "prepare":
            return use_cases.prepare_use_case(
                ctx, project_path=kwargs.get("path", ""), args=kwargs
            )
        if name == "list":
            return use_cases.list_use_case(ctx, kwargs)
        if name == "status":
            result = use_cases.status_use_case(ctx, kwargs)
            # El DeltaGraph es el SSOT del sprint (INV-004): se descarta el
            # fallback legacy de `data/spring/**` para que `status` no pueda
            # mostrar un sprint que `cockpit`/`workflow_state` no ven.
            result["sprint"] = None
            # Resolver el sprint por sesión: con varios sprints activos,
            # `get_active_spring()` es ambiguo y status devolvía `null`.
            sid = str(kwargs.get("session_id") or "")
            chain = root
            if not chain:
                try:
                    pid = kwargs.get("project_id") or kwargs.get("_project_id") or ctx.store.resolve_from_cwd()
                    chain = (ctx.store.resolve(pid) or {}).get("repo_path") if pid else ""
                except Exception:  # noqa: BLE001
                    chain = ""
            if chain:
                try:
                    svc = build_sprint_service(chain)
                    # Misma política que `brief`/`cockpit`/`workflow_state`
                    # (ADR-WALKER-085). Antes `status` resolvía por su cuenta y
                    # podía mostrar un sprint que otros lectores no veían.
                    resolve = getattr(svc, "resolve_context", None)
                    sctx = (
                        resolve(chain, session_id=sid)
                        if callable(resolve)
                        else sprint_context_for(chain, session_id=sid)
                    )
                    # Si no hay sprint en delta, `status` dice "sin sprint"
                    # igual que `cockpit`/`workflow_state` (ADR-WALKER-085).
                    if sctx.found:
                        result["sprint"] = {
                            "sprint_id": sctx.spring_id,
                            "goal": sctx.goal,
                            "status": sctx.status,
                            "session_id": sid,
                            "specs_count": len(sctx.specs),
                            "resolved_by": sctx.resolved_by,
                            "session_linked": sctx.session_linked,
                        }
                        if sctx.diagnostics:
                            result["sprint"]["diagnostics"] = list(sctx.diagnostics)
                            result.setdefault("warnings", [])
                            if "sprint_unattributed" in sctx.diagnostics:
                                result["warnings"].append(
                                    "sprint_unattributed: hay un sprint activo sin "
                                    "sesión dueña (ADR-WALKER-077/085)"
                                )
                        result.pop("message", None)
                    else:
                        result["message"] = "Project — no active sprint (DeltaGraph)"
                except Exception:  # noqa: BLE001 — status no rompe por enriquecimiento
                    svc = None
                # Última sesión del agente en walker (para que el agente pueda
                # explorar de qué trataba). Solo si hay sprint_service.
                try:
                    if svc is not None:
                        pid_for_session = kwargs.get("project_id") or kwargs.get("_project_id") or ctx.store.resolve_from_cwd()
                        last = svc.last_session(pid_for_session) if pid_for_session else None
                        if last:
                            result["last_session"] = {
                                "session_id": last.get("session_id", ""),
                                "task_id": last.get("task_id", ""),
                                "updated_at": last.get("updated_at", ""),
                                "status": last.get("status", ""),
                            }
                except Exception:  # noqa: BLE001 — status no rompe por enriquecimiento
                    pass
            return result
        raise ValueError(f"tool '{name}' sin use case en context project")

    return runner


# ── Memory tool runners (Phase 2 — handler registry) ────────────────────


def _no_project_context_error() -> dict:
    """Error fail-fast (INV-016) ACCIONABLE para las tools que exigen proyecto.

    NO resuelve el proyecto de forma implícita (prohibido: fail-fast, sin
    fallback por CWD - AGENTS.md). En su lugar le dice al agente exactamente
    qué hacer: pasar el parámetro EXPLÍCITO `project_id` y enumera los
    proyectos disponibles para que sepa valores válidos.
    """
    from fast_mcp.shared_kernel.registry_store import RegistryStore

    reg = RegistryStore().read().get("projects", {})
    avail = ", ".join(sorted(reg)) if reg else "(no hay proyectos registrados)"
    return {"error": (
        "No project context. Esta tool MCP exige el parámetro EXPLÍCITO "
        "'project_id' en la llamada (fail-fast: el proyecto NO se infiere del "
        "directorio de trabajo; no hay fallback). Pase 'project_id' con uno de "
        f"estos proyectos registrados: [{avail}]. Si no hay ninguno, registre "
        "uno primero con la tool 'walker_prepare' pasando 'path' al repo. "
        "Ver proyectos con 'walker_list'."
    )}


def _resolve_chain_dir(kwargs: dict) -> str:
    """Resolve chain_dir from kwargs (project_id, _project_id o chain_dir).

    Returns the REPO ROOT (where the actual code lives), not the layout dir
    (.cognitive/projects/<pid>). This is what handlers need to resolve file paths.

    `chain_dir` es el camino que usan las acciones del workflow
    (`ExecutionContext.chain_dir`). Ignorarlo hacía que TODA tool de memoria
    invocada desde una acción devolviera "No project context" — un fallo
    silencioso: `gather_context` y `documenting_knowledge` de DESIGN corrían
    con `warnings` y el CKG nunca se escribía (medido 2026-11-18).
    ``MemoryContext.resolve`` ya valida el proyecto a partir del path.
    """
    from fast_mcp.shared_kernel.paths import get_project_dir

    chain_dir_arg = str(kwargs.get("chain_dir") or "")
    if chain_dir_arg and os.path.isdir(chain_dir_arg):
        return chain_dir_arg

    project_id = kwargs.get("project_id") or kwargs.get("_project_id") or ""
    if not project_id:
        return ""

    # Step 1: project_id → layout dir (.cognitive/projects/<pid>)
    try:
        project_dir = get_project_dir(project_id)
        if project_dir.is_dir():
            # Step 2: read repo_path from project.json (the actual code root)
            manifest = project_dir / "data" / "project.json"
            if manifest.is_file():
                import json
                try:
                    data = json.loads(manifest.read_text())
                    repo_path = data.get("project", {}).get("location", {}).get("repo_path", "")
                    if repo_path and os.path.isdir(repo_path):
                        return repo_path
                except Exception:
                    pass
            return str(project_dir)
    except Exception:
        pass

    # Step 3: fallback — try resolve_paths directly (if already a path)
    try:
        from fast_mcp.shared_kernel.project_support import ProjectService

        paths = ProjectService.resolve_paths(project_id)
        # Un id DESCONOCIDO hacía que `resolve_paths` cayera al repo de walker
        # (mismo bug que el CLI ya corrige comparando el id resuelto). Sin este
        # control, el camino MCP leía/escribía estado del proyecto equivocado.
        resolved_id = getattr(paths, "project_id", "")
        if not resolved_id or resolved_id != project_id:
            return ""
        return str(paths.project_root) if getattr(paths, "project_root", "") else ""
    except Exception:
        pass

    # Nunca devolver el id crudo: se usaría como path relativo y el handler
    # escribiría fuera de todo proyecto. Sin contexto → error accionable.
    return ""


def memory_tool_runner(name: str, root: Path | None = None):
    """Runner de tool del context memories, con contexto YA cableado.

    Regla de coherencia §3bis 2 y 3: phases nunca importa features —
    recibe el callable cerrado sobre su contexto.

    Dispatches to fast_mcp.memories.handlers.dispatch() —
    each tool handler is in its own module (SRP, OCP).
    """
    from fast_mcp.memories.handlers import dispatch
    from fast_mcp.memories.context import MemoryContext

    def runner(**kwargs) -> dict:
        chain_dir = _resolve_chain_dir(kwargs)
        if not chain_dir:
            return _no_project_context_error()

        # AE-INV-07: las lecturas pesadas corren en procesos (el GIL las
        # serializa en hilos). El worker resuelve su propio MemoryContext.
        from fast_mcp.memories import read_pool

        if name in read_pool.TOOLS and read_pool.enabled():
            return read_pool.run(name, chain_dir, kwargs)

        ctx = MemoryContext.resolve(chain_dir)
        if ctx is None:
            return {"error": f"Could not resolve project context for '{chain_dir}'"}

        return dispatch(name, ctx, **kwargs)

    return runner


# ── Spec readiness: no se avanza con specs vacías ──────────────────────
#
# El grafo declara los campos obligatorios de TASK_SPEC, pero los gates
# `all_done` (TASK_REVIEW→ACTIVE) y `start_task` (ACTIVE→DESIGN) no miraban el
# Delta: el agente podía llegar a implementar con las specs en `draft`. Estos
# guards viven en el composition root porque cruzan features (workflow↔delta);
# el motor sigue sin importar Delta (R1).

_INCOMPLETE_SPEC_STATUSES = frozenset({"draft", "rejected"})
# `backlog` es una spec que NO se comprometió en este sprint: no bloquea
# `all_done`, pero tampoco se puede iniciar sin especificarla primero.
_NOT_READY_STATUSES = _INCOMPLETE_SPEC_STATUSES | {"backlog"}
_SKIPPED_SPEC_STATUSES = frozenset({"backlog", "rejected"})


def _spec_validator():
    from fast_mcp.features.delta_tracking.domain.spec_service import SpecValidator

    return SpecValidator


def _sprint_and_specs(sprint_service, session_id: str) -> tuple[str, list[dict]]:
    sprint_id = ""
    if session_id and hasattr(sprint_service, "get_session"):
        sprint_id = str((sprint_service.get_session(session_id) or {}).get("spring_id") or "")
    if not sprint_id:
        active = sprint_service.get_active_spring() or {}
        sprint_id = str(active.get("spring_id") or "")
    specs = sprint_service.get_specs(sprint_id) if sprint_id else []
    return sprint_id, list(specs or [])


def _spec_matches_task(spec: dict, task_id: str) -> bool:
    return (
        str(spec.get("spec_id") or "") == task_id
        or str((spec.get("task_context") or {}).get("task_id") or "") == task_id
    )


def _spec_not_ready_payload(spec: dict, task_id: str, sprint_id: str) -> dict | None:
    spec_id = str(spec.get("spec_id") or task_id)
    status = str(spec.get("status") or "draft")
    errors = _spec_validator().validate_spec(spec)
    if status not in _NOT_READY_STATUSES and not errors:
        return None
    detail = "; ".join(errors) or f"status={status}"
    return {
        "ok": False,
        "action": "spec_not_ready",
        "spec_id": spec_id,
        "status": status,
        "errors": errors,
        "error": (
            f"No se puede iniciar {spec_id} ({sprint_id}): la spec no está "
            f"completa — {detail}. Completala con spec_update "
            "(objective, files, acceptance_criteria) y pasala a 'specified' "
            "con spec_status."
        ),
        "remedy": {
            "fields": f'spec_update {{"spec_id":"{spec_id}","fields":{{...}}}}',
            "status": f'spec_status {{"spec_id":"{spec_id}","status":"specified"}}',
        },
    }


def _start_task_spec_guard(
    chain_dir: str, sprint_service: Any, session_id: str, fallback_task_id: str = ""
) -> dict | None:
    """`start_task` exige una spec completa para la tarea en curso."""
    if sprint_service is None or not hasattr(sprint_service, "get_specs"):
        return None
    task_id = _workflow_task_id(chain_dir) or str(fallback_task_id or "")
    if not task_id and session_id and hasattr(sprint_service, "get_session_plan"):
        try:
            plan = sprint_service.get_session_plan(session_id) or {}
            task_id = str(plan.get("task") or "")
        except Exception:  # noqa: BLE001 — sin plan se reporta abajo
            task_id = ""
    if not task_id:
        return {
            "ok": False,
            "action": "task_not_selected",
            "error": (
                "start_task requiere una tarea en curso. Elegí la spec primero: "
                'spec_start {"spec_id":"SPEC-NNN"} o session_plan {"task":"SPEC-NNN"}.'
            ),
        }
    sprint_id, specs = _sprint_and_specs(sprint_service, session_id)
    if not sprint_id:
        return {
            "ok": False,
            "action": "sprint_not_resolved",
            "error": "start_task: no se pudo resolver el sprint de la sesión.",
        }
    spec = next((s for s in specs if _spec_matches_task(s, task_id)), None)
    if spec is None:
        return {
            "ok": False,
            "action": "spec_not_found",
            "error": (
                f"start_task: la tarea {task_id} no tiene spec en {sprint_id}. "
                "Especificala antes de implementar (spec_update/spec_status)."
            ),
            "spec_id": task_id,
        }
    return _spec_not_ready_payload(spec, task_id, sprint_id)


def _all_done_specs_guard(sprint_service: Any, session_id: str) -> dict | None:
    """`all_done` (TASK_REVIEW→ACTIVE) exige todas las specs comprometidas."""
    if sprint_service is None or not hasattr(sprint_service, "get_specs"):
        return None
    sprint_id, specs = _sprint_and_specs(sprint_service, session_id)
    if not sprint_id:
        return None
    pending: list[dict] = []
    for spec in specs:
        status = str(spec.get("status") or "draft")
        if status in _SKIPPED_SPEC_STATUSES:
            continue
        errors = _spec_validator().validate_spec(spec)
        if status in _INCOMPLETE_SPEC_STATUSES or errors:
            pending.append({
                "spec_id": str(spec.get("spec_id") or "?"),
                "status": status,
                "errors": errors,
            })
    if not pending:
        return None
    listed = ", ".join(
        f"{p['spec_id']}({p['status']})" for p in pending[:8]
    )
    return {
        "ok": False,
        "action": "specs_pending",
        "error": (
            f"all_done bloqueado: {len(pending)} spec(s) sin especificar en "
            f"{sprint_id}: {listed}. Completalas con spec_update/spec_status "
            "antes de pasar el sprint a ACTIVE."
        ),
        "pending": pending,
        "remedy": "spec_update + spec_status status=specified por cada spec",
    }


# ── Resumen del sprint para presentar al cerrar el plan ─────────────────
#
# El workflow no emitía el resumen al pasar a ACTIVE: la transición devolvía
# solo gates y el prompt de ACTIVE truncaba los objetivos. Esta proyección
# expone goal + todas las specs (estado, archivos, criterios) para que el
# agente lo presente al usuario sin otra llamada.


def _sprint_summary(sctx: Any) -> dict[str, Any]:
    return {
        "spring_id": sctx.spring_id,
        "goal": sctx.goal,
        "status": sctx.status,
        "spec_count": len(sctx.specs),
        "specs": [
            {
                "spec_id": s.get("spec_id"),
                "objective": s.get("objective"),
                "status": s.get("status"),
                "files": list(s.get("files") or []),
                "acceptance_criteria": list(s.get("acceptance_criteria") or []),
                "depends_on": list(s.get("depends_on") or []),
            }
            for s in (sctx.specs or [])
        ],
    }


# ── Workflow tool runners (Phase 3 — handler registry) ──────────────────

def workflow_tool_runner(name: str, root: Path | None = None):
    """Runner de tool del context workflow, con contexto YA cableado.

    Regla de coherencia §3bis 2 y 3: phases nunca importa features —
    recibe el callable cerrado sobre su contexto.

    Dispatches to fast_mcp.features.workflow.handlers.dispatch().
    Resolves WorkflowContext per call (not cached).
    """
    from fast_mcp.features.workflow.handlers import dispatch
    from fast_mcp.features.workflow.context import WorkflowContext

    def runner(**kwargs) -> dict:
        chain_dir = _resolve_chain_dir(kwargs)
        if not chain_dir:
            return _no_project_context_error()

        # ADR-084: submit/advance bloqueantes ante gaps no remediados.
        if name in ("submit", "advance"):
            session_id = str(kwargs.get("session_id") or kwargs.get("_session_id") or "")
            if session_id and not kwargs.get("force"):
                try:
                    import importlib

                    _mod = importlib.import_module(
                        "fast_mcp.features.event_tracing.application.services.trace_factory"
                    )
                    build_trace_service = _mod.build_trace_service
                    _kmod = importlib.import_module("fast_mcp.memories.handlers.knowledge")
                    _project_flight_state = _kmod._project_flight_state

                    service = build_trace_service(chain_dir)
                    if service is not None:
                        events = [
                            e for e in service.load_traces(chain_dir)
                            if str((getattr(e, "metadata", None) or {}).get("session_id", "")) == session_id
                        ]
                        flight = _project_flight_state(events, chain_dir, session_id)
                        gaps = flight.get("gaps") or []
                        if gaps:
                            detail = "; ".join(
                                str(g.get("detail", g) if isinstance(g, dict) else g)
                                for g in gaps[:3]
                            )
                            return {
                                "ok": False,
                                "error": (
                                    f"submit/advance bloqueado: hay gaps sin remediar: {detail}. "
                                    "Corregí o pasá force=true."
                                ),
                            }
                except Exception:  # noqa: BLE001
                    pass

        # Inject the TaskRegistry (Sprint 5 — engine anémico). Cross-feature
        # wiring is legal ONLY here (composition root, R1/§3bis): the workflow
        # feature never imports the tasks feature itself.
        sprint_service = _build_sprint_service(chain_dir)
        task_registry = _build_task_registry(sprint_service=sprint_service)

        # W5 (ADR-076): inyectar adapter CPG para task handlers (fail-fast,
        # no guessing). Si no hay proyecto resolvible o el adapter falla, se
        # deja como None: los handlers lo tratan como ausente (opcional).
        cpg_adapter = None
        if chain_dir:
            try:
                from fast_mcp.features.code_analysis.application.services.cpg_service import CpgService
                cpg_service = CpgService.from_chain_dir(chain_dir)
                if cpg_service:
                    cpg_adapter = cpg_service.get_analysis_adapter(chain_dir)
            except Exception as exc:
                logger.debug("No se pudo construir cpg_adapter para task handlers (%s)", exc)

        # Enriquecimiento de prompts con el conocimiento del proyecto: el
        # searcher CKG se construye AQUÍ (composition root) y se inyecta; la
        # feature workflow no importa cognitive_knowledge (R1).
        ckg_searcher = None
        if chain_dir:
            try:
                from fast_mcp.features.cognitive_knowledge import ckg_service
                from fast_mcp.shared_kernel.project_support import ProjectService

                paths = ProjectService.resolve_paths(chain_dir)
                ckg_searcher = ckg_service(str(paths.data_dir))
            except Exception as exc:
                logger.debug("No se pudo construir ckg_searcher para prompts (%s)", exc)

        # Tracer REAL para el motor: sin esto `has_kg_query`/`has_skill_load`
        # devuelven False siempre y los gates que dependen de la traza son
        # insatisfacibles (medido 2026-11-17: el gate `required_tools` de
        # IMPLEMENT.WORK bloqueaba para siempre).
        workflow_tracer = None
        if chain_dir:
            try:
                from fast_mcp.features.event_tracing.application.services.trace_factory import (
                    build_trace_service,
                )
                from fast_mcp.features.event_tracing.infrastructure.cognitive_tracer import (
                    CognitiveTracer,
                )

                service = build_trace_service(chain_dir)
                workflow_tracer = CognitiveTracer(service) if service else None
            except Exception as exc:  # noqa: BLE001
                logger.warning("tracer del workflow no disponible (%s: %s)", type(exc).__name__, exc)

        from fast_mcp.features.workflow.infrastructure.side_effect_engine import SideEffectEngine

        ctx = WorkflowContext.resolve(
            chain_dir,
            task_registry=task_registry,
            cpg_adapter=cpg_adapter,
            ckg_searcher=ckg_searcher,
            tracer=workflow_tracer,
            side_effects=SideEffectEngine(_build_side_effect_registry()),
        )
        if ctx is None:
            return {"error": f"Could not resolve workflow for '{chain_dir}'"}

        gate_name = str(kwargs.get("gate") or "")
        session_id = str(kwargs.get("session_id") or kwargs.get("_session_id") or "")

        # Los gates que cruzan hacia ejecución exigen specs escritas. El grafo
        # declara los campos; sin este chequeo el agente podía saltarlos.
        # `submit` en TASK_REVIEW puede traer `decision=all_done`, que el
        # orquestador cruza por dentro (decision_map): el guard tiene que
        # aplicarse ANTES de dispatch, no sólo en la tool `transition`.
        submit_decision = str(
            kwargs.get("decision")
            or (kwargs.get("_payload") or {}).get("decision")
            or ""
        )
        if name == "submit" and submit_decision == "all_done":
            blocked = _all_done_specs_guard(sprint_service, session_id)
            if blocked:
                return blocked
        if name == "transition" and gate_name == "all_done":
            blocked = _all_done_specs_guard(sprint_service, session_id)
            if blocked:
                return blocked
        if name == "transition" and gate_name == "start_task":
            blocked = _start_task_spec_guard(
                chain_dir, sprint_service, session_id, str(kwargs.get("task_id") or "")
            )
            if blocked:
                return blocked

        # `back_to_active` NO reabre sprints: `closed` es terminal (ADR-078).
        # Desde SPRINT.CLOSE solo tiene sentido si hay un sprint VIVO al cual
        # volver; si no, el resultado sería un workflow ACTIVE con el sprint
        # cerrado (incoherencia silenciosa). La salida es crear uno nuevo.
        if name == "transition" and str(kwargs.get("gate") or "") == "back_to_active":
            from fast_mcp.features.workflow.domain.execution import (
                ExecutionContext as _WfExecCtx,
            )

            st = ctx.orchestrator.status(
                _WfExecCtx(session_id="", trace_id="", span_id="", chain_dir=chain_dir)
            ) or {}
            pos = f"{st.get('node') or ''}.{st.get('sub_state') or ''}"
            active = None
            if pos == "SPRINT.CLOSE" and sprint_service is not None:
                try:
                    active = sprint_service.get_active_spring()
                except Exception as exc:  # noqa: BLE001 — se reporta como ausencia
                    logger.warning("back_to_active: sprint activo ilegible (%s)", exc)
            reason = back_to_active_guard(pos, active)
            if reason:
                return {"ok": False, "action": "needs_sprint", "error": reason}

        result = dispatch(name, ctx, **kwargs)

        # Proyección del sprint al cerrar el plan: cuando el flujo entra en
        # ACTIVE (all_done), el agente necesita el resumen completo (goal +
        # todas las specs) para presentarlo al usuario sin otra llamada.
        if (
            isinstance(result, dict)
            and result.get("ok")
            and result.get("to_sub_state") == "ACTIVE"
        ):
            try:
                sid = session_id or str(kwargs.get("session_id") or "")
                sctx = (
                    sprint_service.resolve_context(chain_dir, session_id=sid)
                    if sprint_service is not None and hasattr(sprint_service, "resolve_context")
                    else sprint_context_for(chain_dir, session_id=sid)
                )
                result["sprint_summary"] = _sprint_summary(sctx)
            except Exception as exc:  # noqa: BLE001 — el estado no se cae por la proyección
                logger.debug("sprint_summary: no disponible (%s)", exc)

        # El workflow y el DeltaGraph son dos stores: si no se publica el join,
        # el agente ve `SPRINT.ACTIVE` con `Goal:` vacío y `0/0` mientras
        # `status` muestra un sprint con specs. El bloque `sprint` sale del
        # MISMO resolver que el prompt (ADR-WALKER-085).
        if (
            name in ("workflow_state", "help")
            and isinstance(result, dict)
            and result.get("ok")
        ):
            sid = str(kwargs.get("session_id") or "")
            try:
                sctx = (
                    sprint_service.resolve_context(chain_dir, session_id=sid)
                    if sprint_service is not None
                    else sprint_context_for(chain_dir, session_id=sid)
                )
                result.setdefault("sprint", sctx.to_dict())
                result["sprint_summary"] = _sprint_summary(sctx)
            except Exception as exc:  # noqa: BLE001 — el estado no se cae por el join
                logger.debug("workflow: sprint del estado no disponible (%s)", exc)

        return result

    return runner


# ── Delta tool runners (Sprint 5.2 — handler registry) ─────────────

def delta_tool_runner(name: str, root: Path | None = None):
    """Runner de tool del context delta_tracking, con contexto YA cableado.

    Regla de coherencia §3bis 2 y 3: phases nunca importa features —
    recibe el callable cerrado sobre su contexto.

    Dispatches to fast_mcp.features.delta_tracking.handlers.dispatch_delta().
    """
    from fast_mcp.features.delta_tracking.handlers import dispatch_delta

    def runner(**kwargs) -> dict:
        chain_dir = _resolve_chain_dir(kwargs)
        if not chain_dir:
            return _no_project_context_error()

        # Repo-backed service injected by the composition root (R1/§3bis):
        # handlers never self-construct an anemic SprintService (INV-016).
        sprint_service = _build_sprint_service(chain_dir)
        return dispatch_delta(name, {**kwargs, "_sprint_service": sprint_service})

    return runner


# ── Trace tool runners (Sprint 5.3 — handler registry) ─────────────────
def trace_tool_runner(name: str, root: Path | None = None):
    """Runner de tool del context event_tracing, con contexto YA cableado.

    Regla de coherencia §3bis 2 y 3: phases nunca importa features —
    recibe el callable cerrado sobre su contexto.

    Dispatches to fast_mcp.features.event_tracing.handlers.dispatch_trace().
    """
    from fast_mcp.features.event_tracing.handlers import dispatch_trace

    def runner(**kwargs) -> dict:
        chain_dir = _resolve_chain_dir(kwargs)
        if not chain_dir:
            return _no_project_context_error()

        # Wiring único en trace_factory (cacheado por proyecto): el tracer del
        # workflow y este runner deben compartir el mismo CognitiveGraph, si no
        # la guarda de generación rechaza writes cruzados y se pierden eventos.
        from fast_mcp.features.event_tracing.application.services.trace_factory import (
            build_trace_service,
        )

        trace_svc = build_trace_service(chain_dir)
        if trace_svc is None:
            return {"error": "Could not initialize CognitiveGraph"}
        ctx = {"trace_service": trace_svc, "chain_dir": chain_dir}

        return dispatch_trace(name, ctx, **kwargs)

    return runner


# ── Task/SideEffect registry (Sprint 5.4) ─────────────────────────────

def _build_delta_repo(chain_dir: str):
    """Build the repo-backed DeltaRepository (composition root only).

    Creates the CognitiveGraph via the canonical factory (config-driven
    sqlite, ADR-063/INV-016 fail-fast) and registers the 'delta' index.
    Cross-feature wiring is legal ONLY here (R1/§3bis).

    INV-016 (regla 3): `build_cognitive_graph` exige el data_dir YA resuelto
    (contrato de su docstring). `chain_dir` aquí es el REPO ROOT (contrato de
    `_resolve_chain_dir`) — pasarle el repo root a `load_memory_connection`
    ancla la DB al lugar equivocado (vector histórico de la base parásita
    `<repo>/cognitive.sqlite3`). Se resuelve el data_dir canónico del proyecto
    vía ProjectService.resolve_paths() (ADR-WALKER-054 D4).
    """
    from fast_mcp.shared_kernel.cognitive_graph.config import load_memory_connection
    from fast_mcp.shared_kernel.cognitive_graph import build_cognitive_graph
    from fast_mcp.shared_kernel.cognitive_graph.schemas import (
        PRODUCTION_INDEX_SCHEMAS,
    )
    from fast_mcp.shared_kernel.project_support import ProjectService
    from fast_mcp.features.delta_tracking.infrastructure.cognitive_delta_repo import (
        CognitiveDeltaRepository,
    )

    paths = ProjectService.resolve_paths(chain_dir)
    conn = load_memory_connection(paths.data_dir)
    cg = build_cognitive_graph(paths.data_dir, conn=conn)
    try:
        cg.register_index("delta", PRODUCTION_INDEX_SCHEMAS["delta"])
    except Exception:
        pass  # already registered
    return CognitiveDeltaRepository(cg)


def _build_sprint_service(chain_dir: str):
    """Build the repo-backed SprintService for the given chain_dir.

    Sprint 5.2: the delta index is now wired — sprint/spec mutations persist
    to the CognitiveGraph (no more anemic in-memory service). Fail-fast
    (INV-016): if the CognitiveGraph can't be built the error propagates to
    the caller (structured error); never a silent no-op write.
    """
    if not chain_dir:
        return None
    from fast_mcp.features.delta_tracking.application.services.sprint_service import (
        SprintService,
    )

    return SprintService(project_root=chain_dir, repo=_build_delta_repo(chain_dir))


def _sprint_service_factory(chain_dir: str):
    """Factory estable para la DI de tasks (no el wrapper monkeypatcheable).

    `build_sprint_service` es API pública y los tests la reemplazan; si el
    registro global de tasks capturara esa versión, el fake sobreviviría al
    test y contaminaría los siguientes (medido: captura de knowledge con el
    sprint de un test anterior).
    """
    return _build_sprint_service(chain_dir)


def build_sprint_service(chain_dir: str):
    """API pública del composition root para leer el delta desde otra capa.

    `SprintService.from_chain_dir` exige un `path_resolver` inyectado; sin él
    devuelve None y todo `task_id` parecía no existir. Este helper arma el
    servicio con su repo, igual que el runner de delta.
    """
    return _build_sprint_service(chain_dir)


def sprint_context_for(
    chain_dir: str,
    *,
    session_id: str = "",
    spring_id: str = "",
    allow_active_fallback: bool = True,
):
    """Sprint corriente resuelto con la política ÚNICA (ADR-WALKER-085).

    Punto de entrada para toda superficie (workflow, status, brief, cockpit,
    session_context). Evita que cada capa invente su propio orden de resolución
    o derive el sprint del basename. El composition root es el único lugar que
    conoce el DeltaGraph; las features reciben el `SprintContext` ya resuelto.
    """
    from fast_mcp.features.delta_tracking.application.services.sprint_context import (
        SprintContext,
        resolve_sprint_context,
    )

    try:
        service = build_sprint_service(chain_dir)
    except Exception as exc:  # noqa: BLE001 — lectura: se declara, no se oculta
        return SprintContext(
            session_id=session_id,
            diagnostics=(f"sprint_service_error:{type(exc).__name__}",),
        )
    return resolve_sprint_context(
        service,
        chain_dir,
        session_id=session_id,
        spring_id=spring_id,
        allow_active_fallback=allow_active_fallback,
    )


def ckg_for(chain_dir: str):
    """CKG del proyecto resuelto por path (query/lectura).

    R1: los efectos laterales de tasks no pueden importar cognitive_knowledge;
    piden el gráfico al composition root.
    """
    if not chain_dir:
        return None
    from fast_mcp.features.cognitive_knowledge import ckg_service
    from fast_mcp.memories.context import MemoryContext

    ctx = MemoryContext.resolve(chain_dir)
    if ctx is None or not ctx.data_dir:
        return None
    return ckg_service(ctx.data_dir)


def ckg_save_nodes(
    chain_dir: str,
    nodes: list[dict],
    task_id: str = "",
    session_id: str = "",
) -> dict:
    """Persiste nodos de conocimiento y devuelve un dict plano.

    Traduce el `Result` del servicio a `{ok, node_ids, ...}` para que un efecto
    no dependa de `returns` ni de la clase del feature.
    """
    if not chain_dir:
        return {"ok": False, "error": "chain_dir no disponible"}
    from fast_mcp.features.cognitive_knowledge.application.services.ckg_write import (
        CkgWriteService,
    )
    from returns.pipeline import is_successful

    result = CkgWriteService.save_nodes(
        chain_dir, nodes, task_id=task_id, session_id=session_id
    )
    if not is_successful(result):
        return {"ok": False, "error": str(result.failure())}
    body = dict(result.value_or({}) or {})
    if body.get("error"):
        return {"ok": False, "error": str(body["error"])}
    body["ok"] = True
    return body


def ckg_delete_by_metadata(chain_dir: str, key: str, value: str) -> dict:
    """Compensación: borra nodos del CKG por metadata key=value."""
    if not chain_dir:
        return {"ok": False, "error": "chain_dir no disponible"}
    from fast_mcp.features.cognitive_knowledge.application.services.ckg_write import (
        CkgWriteService,
    )

    return CkgWriteService.delete_by_metadata(chain_dir, key, value)


def _trace_task_notes(name, ctx, result) -> None:
    """Traza las degradaciones del resultado de un task (P1.19).

    `warnings`, `available: False` y `findings[].error` son señales que hoy
    sólo ve quien lee el resultado: si el agente no lo lee, el motor "hizo" algo
    que no hizo. El reporte de flight control las cuenta desde el trace.
    Observador best-effort: nunca forma parte del task.
    """
    try:
        from fast_mcp.features.tasks.domain.degradations import (
            degradation_event,
            result_notes,
        )
        from fast_mcp.features.tasks.handlers import _di as tasks_di

        notes = result_notes(result)
        if not notes:
            return
        chain_dir = str(getattr(ctx, "chain_dir", "") or "")
        session_id = str(getattr(ctx, "session_id", "") or "")
        task_id = str(getattr(ctx, "task_id", "") or "")
        for note in notes:
            tasks_di.push_trace(
                chain_dir, degradation_event(name, note, task_id=task_id, session_id=session_id)
            )
    except Exception:  # noqa: BLE001 — observador, nunca parte del task
        logger.debug("_trace_task_notes falló para '%s'", name, exc_info=True)


def _after_task(name, ctx, result) -> None:
    """Observadores de fin de task: hooks del proyecto + trace de degradaciones."""
    _emit_task_after(name, ctx, result)
    _trace_task_notes(name, ctx, result)


def _emit_task_after(name, ctx, result) -> None:
    """Notifica `task.after` al bus de hooks del proyecto del task.

    Composition es el único lugar que puede cruzar features (R1); el registry
    recibe esta función inyectada y no conoce ni el bus ni las rutas.
    """
    chain_dir = getattr(ctx, "chain_dir", "")
    if not chain_dir:
        return
    try:
        from fast_mcp.shared_kernel.hooks import HookBus
        from fast_mcp.shared_kernel.project_support import ProjectService

        # `ctx.chain_dir` puede ser el layout cognitivo
        # (<repo>/.cognitive/projects/<pid>), no la raíz del repo: los hooks se
        # declaran en <repo>/.cognitive/hooks y el trust en <repo>/.cognitive/
        # config.json, así que hay que resolver el project_root.
        try:
            project_root = str(ProjectService.resolve_paths(chain_dir).project_root or chain_dir)
        except Exception:  # noqa: BLE001 — sin layout, se usa chain_dir tal cual
            project_root = chain_dir

        HookBus.load(project_root).emit("task.after", {
            "task": name,
            "task_id": getattr(ctx, "task_id", ""),
            "session_id": getattr(ctx, "session_id", ""),
            "ok": bool(result.ok),
            "error": result.error,
            "chain_dir": chain_dir,
        })
    except Exception:  # noqa: BLE001 — observador, nunca parte del task
        pass


def reset_workflow_after_close(chain_dir: str) -> dict:
    """Saca el workflow de SPRINT.CLOSE sin reabrir el sprint cerrado.

    Se usa al crear un sprint nuevo después de un cierre: si el estado se queda
    en CLOSE, el auto-advance ejecuta `close_sprint` y cierra el sprint recién
    creado (0 tareas → cierre válido, así que el daño es silencioso).
    """
    from fast_mcp.features.workflow.context import WorkflowContext
    from fast_mcp.features.workflow.domain.execution import ExecutionContext

    wctx = WorkflowContext.resolve(chain_dir)
    if wctx is None:
        return {"ok": False, "error": "workflow no resoluble"}
    exec_ctx = ExecutionContext(
        session_id="", trace_id="", span_id="", chain_dir=chain_dir
    )
    st = wctx.orchestrator.status(exec_ctx) or {}
    pos = f"{st.get('node') or ''}.{st.get('sub_state') or ''}"
    if pos != "SPRINT.CLOSE":
        return {"ok": True, "changed": False, "position": pos}
    res = wctx.orchestrator.transition(
        exec_ctx,
        gate="back_to_active",
        reason="nuevo sprint de la sesión (post-cierre)",
    )
    if not res.get("ok"):
        return {
            "ok": False,
            "error": res.get("error", "no se pudo reposicionar el workflow"),
        }
    return {"ok": True, "changed": True, "position": res.get("position", "")}


def back_to_active_guard(position: str, active: Any) -> str | None:
    """Motivo por el que `back_to_active` no procede, o None si es válido.

    `closed` es terminal (ADR-078): el gate no reabre sprints. Desde
    SPRINT.CLOSE solo tiene sentido si hay un sprint VIVO al cual volver; si no,
    quedaría un workflow ACTIVE sobre un sprint cerrado sin aviso.
    """
    if position != "SPRINT.CLOSE" or active is not None:
        return None
    return (
        "back_to_active no reabre un sprint cerrado (closed es terminal). "
        "Creá un sprint nuevo con session_bind mode=new: reposiciona el "
        "workflow y deja un sprint vivo."
    )


def _build_task_registry(sprint_service=None):
    """Build and populate a TaskRegistry with all registered handlers."""
    from fast_mcp.features.code_analysis.application.use_cases.sync_cpg import (
        sync_project_cpg,
    )
    from fast_mcp.features.tasks.domain.task_registry import TaskRegistry
    from fast_mcp.features.tasks.handlers import register_all_tasks

    registry = TaskRegistry(on_task_done=_after_task)
    # cpg_sync: composition es el único que puede cruzar features (R1); el
    # handler de tasks la recibe inyectada en vez de importar code_analysis.
    # Baseline del workflow: la factory se inyecta acá porque la feature tasks
    # no puede importar code_analysis (R1).
    try:
        from fast_mcp.features.code_analysis.application.baseline_service import (
            BaselineService,
        )
        from fast_mcp.features.tasks.handlers import baseline as baseline_handlers
        from fast_mcp.features.tasks.handlers import implement as implement_handlers

        baseline_handlers.configure(lambda chain_dir: BaselineService(chain_dir))
        # `implement_task` persiste la spec en el delta: sin esta factory el
        # delta queda vacío y el ancla de tarea del contexto no existe.
        # Se inyecta la factory ESTABLE (no el wrapper público): un test que
        # monkeypatchea `build_sprint_service` no debe quedar capturado en el
        # registro global de tasks y contaminar tests posteriores.
        implement_handlers.configure(_sprint_service_factory)

        # verify_predicates lee la spec del delta para recuperar predicates:
        # sin esta factory, el gate no puede validar nada (bug medido).
        from fast_mcp.features.tasks.handlers import _di as tasks_di
        tasks_di.configure_sprint(_sprint_service_factory)
        from fast_mcp.features.workflow.context import WorkflowContext
        from fast_mcp.features.workflow.domain.execution import ExecutionContext

        def _record_task_id(chain_dir: str, task_id: str) -> bool:
            from fast_mcp.features.workflow.infrastructure.side_effect_engine import SideEffectEngine

            wctx = WorkflowContext.resolve(
                chain_dir, side_effects=SideEffectEngine(_build_side_effect_registry())
            )
            if wctx is None:
                return False
            return wctx.orchestrator.remember_task(
                ExecutionContext(session_id="mcp", trace_id="", span_id="", chain_dir=chain_dir),
                task_id,
            )

        tasks_di.configure_task_recorder(_record_task_id)
        tasks_di.configure_workflow_reset(reset_workflow_after_close)

        from fast_mcp.features.cognitive_knowledge.application.services.ckg_write import (
            CkgWriteService,
        )

        def _save_knowledge(chain_dir, nodes, task_id="", session_id=""):
            result = CkgWriteService.save_nodes(
                chain_dir, nodes, task_id=task_id, session_id=session_id
            )
            from returns.pipeline import is_successful

            return result.value_or({}) if is_successful(result) else {"error": str(result.failure())}

        tasks_di.configure_knowledge_writer(_save_knowledge)

        from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
            CkgReadService,
        )

        def _session_facts(chain_dir: str) -> list:
            """Nodos `session` activos para la retención de la captura (P1.5)."""
            return CkgReadService.query_active(chain_dir, "session", size=1000)

        tasks_di.configure_knowledge_reader(_session_facts)

        from fast_mcp.features.event_tracing.application.services.trace_factory import (
            build_trace_service,
        )

        def _session_events(chain_dir: str, session_id: str) -> list:
            service = build_trace_service(chain_dir)
            if service is None:
                return []
            return [
                e for e in service.load_traces(chain_dir)
                if str((e.metadata or {}).get("session_id", "")) == session_id
            ]

        tasks_di.configure_trace_reader(_session_events)

        def _push_trace(chain_dir: str, event: dict) -> bool:
            """Verificación registrada por la TOOL (P1.11).

            El plugin hidrata `flight_state` desde el trace: si walker escribe
            acá el verde, el cierre no depende de que el hook vea el comando.
            """
            service = build_trace_service(chain_dir)
            if service is None:
                return False
            result = service.push_event(chain_dir, dict(event))
            return bool(isinstance(result, dict) and result.get("ok"))

        tasks_di.configure_trace_writer(_push_trace)

        from fast_mcp.features.delta_tracking.domain.spec_service import SpecValidator

        tasks_di.configure_spec_validator(
            lambda spec: SpecValidator.validate_spec(spec)
        )

        # F0b (ADR-WALKER-086): el schema del WorkContract vive en
        # delta_tracking/domain; tasks no puede importarlo (R1). Se inyecta acá
        # el validador puro para que capture/spec_status lo usen.
        from fast_mcp.features.delta_tracking.domain.work_contract import (
            validate_work_contract,
        )

        tasks_di.configure_contract_validator(validate_work_contract)
    except Exception as exc:  # noqa: BLE001
        logger.warning("baseline handler no configurado (%s: %s)", type(exc).__name__, exc)

    register_all_tasks(
        registry, sprint_service=sprint_service, cpg_sync=sync_project_cpg
    )
    return registry


def _build_side_effect_registry():
    """Build and populate a SideEffectRegistry with all registered handlers."""
    from fast_mcp.features.tasks.domain.side_effect_registry import SideEffectRegistry
    from fast_mcp.features.tasks.side_effects import register_all_side_effects

    registry = SideEffectRegistry()
    register_all_side_effects(registry)
    return registry


def _workflow_task_id(chain_dir: str) -> str:
    """`task_id` recordado en el estado del workflow, o ""."""
    if not chain_dir:
        return ""
    try:
        from fast_mcp.features.workflow.context import WorkflowContext

        from fast_mcp.features.workflow.infrastructure.side_effect_engine import SideEffectEngine

        wctx = WorkflowContext.resolve(
            chain_dir, side_effects=SideEffectEngine(_build_side_effect_registry())
        )
        if wctx is None:
            return ""
        state = wctx.orchestrator._load_state()
        return str(((state.context if state else {}) or {}).get("task_id", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def task_tool_runner(name: str, root: Path | None = None):
    """Runner de tool del context tasks, con contexto YA cableado.

    Regla de coherencia §3bis 2 y 3: phases nunca importa features —
    recibe el callable cerrado sobre su contexto.

    Dispatches via execute_public_task → TaskRegistry.
    """
    from fast_mcp.features.workflow.application.task_dispatcher import execute_public_task

    def runner(**kwargs) -> dict:
        chain_dir = _resolve_chain_dir(kwargs)
        if not chain_dir:
            return _no_project_context_error()

        # Build TaskRegistry (lazy, per call)
        sprint_service = _build_sprint_service(chain_dir)
        # Configurar _di para que los handlers (verify_predicates, spec_predicates, etc.)
        # que leen el sprint service via _di.sprint_service() lo encuentren en modo CLI.
        # Sin esto, verify_predicates falla con "predicates vacíos" aunque la spec
        # tenga predicados (bug de canal).
        try:
            import fast_mcp.features.tasks.handlers._di as _tasks_di
            _tasks_di.configure_sprint(lambda _cd: sprint_service)
        except Exception:  # noqa: BLE001
            pass
        registry = _build_task_registry(sprint_service=sprint_service)

        project_id = kwargs.get("project_id", "") or kwargs.get("_project_id", "")
        task_data = {k: v for k, v in kwargs.items() if k not in ("project_id",)}

        # La tarea declarada vive en el estado del workflow. Si el llamador no
        # repite el id, se toma de ahí: es lo que permite que `verify` y
        # `run_tests` operen sobre LA tarea en curso y no sobre una heurística.
        if not task_data.get("task_id"):
            current = _workflow_task_id(chain_dir)
            if current:
                task_data["task_id"] = current

        return execute_public_task(
            name=name,
            project_id=project_id,
            chain_dir=chain_dir,
            task_data=task_data,
            registry=registry,
            sprint_service=sprint_service,
        )

    return runner