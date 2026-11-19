"""handlers/session.py — `session_bind`: sesión de chat → sprint (delta-sprint).

Protocolo de continuidad. La sesión es la unidad de trabajo del agente; el
sprint es la unidad de trabajo del proyecto. Este handler las enlaza sin que el
usuario tenga que crear nada:

  - mode="auto"     SOLO LEE. Responde una de tres cosas:
                      bound             la sesión ya tiene sprint
                      resume_available  hay trabajo previo en el proyecto
                      no_work           no hay nada previo
                    Con `suggestion` redactada para que el agente la use con el
                    usuario (continuar el goal o empezar uno nuevo).
  - mode="new"      crea el sprint de la sesión (goal = primer pedido).
  - mode="continue" engancha esta sesión al sprint del trabajo previo y la deja
                    registrada como hija (context.session_ids / parent_session_id).

Reglas:
  * idempotente: si la sesión ya tiene sprint, `new`/`continue` no crean otro.
  * el sprint guarda `session_id` (nativo en Spring) y la lista de sesiones en
    `context.session_ids`, que es lo que permite relacionar tareas ↔ archivos ↔
    conocimiento ↔ trazas a través de la sesión.

R1: tasks no importa delta_tracking; usa la factory inyectada en `_di`.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult
from fast_mcp.features.tasks.handlers import _di
from fast_mcp.features.tasks.handlers.spec_derive import derive



logger = logging.getLogger(__name__)

def _short_hash(text: str) -> str:
    return hashlib.sha1((text or "session").encode("utf-8")).hexdigest()[:8]


def _owned_sessions(spring: dict) -> list[str]:
    ctx = spring.get("context") or {}
    sessions = list(ctx.get("session_ids") or [])
    primary = str(spring.get("session_id") or "")
    if primary and primary not in sessions:
        sessions.insert(0, primary)
    return sessions


def _owns(spring: dict, session_id: str) -> bool:
    return bool(session_id) and session_id in _owned_sessions(spring)


def _tasks_of(service: Any, spring_id: str) -> list[dict]:
    out: list[dict] = []
    for spec in service.get_specs(spring_id) or []:
        tc = spec.get("task_context") or {}
        out.append(
            {
                "task_id": tc.get("task_id") or spec.get("spec_id") or "",
                "objective": spec.get("objective") or "",
                "status": spec.get("status") or "",
                "files": spec.get("files") or [],
                "session_id": tc.get("session_id") or "",
            }
        )
    return out


def _summary(service: Any, spring: dict) -> dict:
    spring_id = str(spring.get("spring_id") or spring.get("id") or "")
    tasks = _tasks_of(service, spring_id)
    ctx = spring.get("context") or {}
    return {
        "spring_id": spring_id,
        "goal": spring.get("goal") or "",
        "status": spring.get("status") or "",
        "session_id": spring.get("session_id") or "",
        "sessions": _owned_sessions(spring),
        "parent_session_id": ctx.get("parent_session_id") or "",
        "tasks": tasks,
        "tasks_count": len(tasks),
    }


def _suggestion(summary: dict, decision: str) -> str:
    goal = summary.get("goal") or "(sin goal)"
    tasks = summary.get("tasks_count", 0)
    if decision == "bound":
        paused = (
            " OJO: el sprint está PAUSADO; reanimalo con sprint_status status=active."
            if str(summary.get("status") or "") == "paused"
            else ""
        )
        return (
            f"Esta sesión ya pertenece a {summary['spring_id']} (goal: {goal}, "
            f"{tasks} tarea(s)). No crees otro sprint: seguí declarando con implement_task.{paused}"
        )
    if decision == "resume_available":
        last = summary.get("session_id") or (summary.get("sessions") or [""])[0]
        return (
            f"Hay trabajo previo en el proyecto: {summary['spring_id']} (goal: {goal}), "
            f"{tasks} tarea(s), última sesión {last or '(sin id)'}. "
            "Preguntale al usuario si continúa ese goal o empieza uno nuevo. "
            "Si continúa: session_bind mode=continue. Si es nuevo: session_bind mode=new."
        )
    return (
        "No hay trabajo previo en el proyecto. Si esto es una implementación, "
        "creá el delta-sprint de esta sesión con session_bind mode=new "
        "(objective = el pedido del usuario) y después declarás la tarea con implement_task."
    )


def session_bind(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    session_id = str(data.get("session_id") or ctx.session_id or "").strip()
    if not session_id or session_id == "mcp":
        return TaskResult.failure("session_bind: session_id requerido (el id real de la sesión)")
    mode = str(data.get("mode") or "auto").strip().lower()
    if mode not in ("auto", "new", "continue"):
        return TaskResult.failure("session_bind: mode debe ser auto|new|continue")
    objective = str(data.get("objective") or "").strip()
    parent = str(data.get("parent_session_id") or "").strip()
    project_id = str(data.get("project_id") or "").strip()
    # `sprint_id` es el nombre que usan los prompts y el humano; el dominio
    # quedó en `spring_id`. Se aceptan los dos para no fallar por sinónimo.
    explicit_spring = str(data.get("spring_id") or data.get("sprint_id") or "").strip()

    service = _di.sprint_service(ctx.chain_dir)
    if service is None:
        return TaskResult.failure("session_bind: sprint service no disponible para este proyecto")

    springs = service.list_springs() or []
    # El registro de la sesión es la fuente O(1). `list_springs` tiene tope
    # (medido: 50) y un sprint recién creado queda fuera, así que `mine`
    # salía None y `session_bind` decía "no tiene sprint" (rompía el harness
    # y cualquier proyecto con muchos sprints).
    mine = None
    if hasattr(service, "get_session"):
        try:
            record = service.get_session(session_id) or {}
            own_spring = str(record.get("spring_id") or "")
            if own_spring and hasattr(service, "get_spring"):
                mine = service.get_spring(own_spring)
        except Exception:  # noqa: BLE001 — fallback a la lista
            mine = None
    if mine is None:
        mine = next((s for s in springs if _owns(s, session_id)), None)
    # `closed` es terminal para la sesión: no se reutiliza ni se "continúa".
    # Se conserva aparte para informar el estado en `auto` sin reportarlo bound.
    closed_mine = None
    if mine is not None and str(mine.get("status") or "") == "closed":
        closed_mine = mine
        mine = None
    active = next((s for s in springs if str(s.get("status") or "") == "active"), None)
    if active is None and springs:
        # El fallback existe para ofrecer un sprint en `planning` al reanudar,
        # pero NUNCA uno cerrado: cerrar es terminal y ofrecerlo como
        # "trabajo previo" era la mentira que dejaba la sesión sin salida.
        candidates = [s for s in springs if str(s.get("status") or "") != "closed"]
        active = candidates[-1] if candidates else None

    explicit = service.get_spring(explicit_spring) if explicit_spring else None
    if explicit_spring and explicit is None:
        return TaskResult.failure(f"session_bind: sprint desconocido {explicit_spring!r}")

    base = {"mode": mode, "session_id": session_id, "project_id": project_id}
    if explicit_spring:
        base["requested_spring_id"] = explicit_spring

    def _join(target: dict) -> dict:
        """Suma la sesión a un sprint existente (del proyecto o de la sesión)."""
        spring_id = str(target.get("spring_id") or target.get("id") or "")
        context = dict(target.get("context") or {})
        sessions = _owned_sessions(target)
        if session_id not in sessions:
            sessions.append(session_id)
        context["session_ids"] = sessions
        if not context.get("parent_session_id"):
            previous = str(target.get("session_id") or "") or (sessions[0] if sessions else "")
            if previous and previous != session_id:
                context["parent_session_id"] = previous
        service.update_spring(spring_id, {"context": context})
        updated = service.get_spring(spring_id) or target
        summary = _summary(service, updated)
        service.open_session(
            session_id,
            project_id=project_id,
            parent_session_id=context.get("parent_session_id") or "",
            spring_id=spring_id,
            goal=summary.get("goal") or "",
            context={"bound_by": "session_bind/join"},
        )
        # Plan inicial si la sesión no tenía uno (p. ej. sprint del roadmap).
        if hasattr(service, "get_session_plan") and not service.get_session_plan(session_id):
            service.update_session_plan(
                session_id,
                {
                    "goal": summary.get("goal") or "",
                    "acceptance": [],
                    "task": "",
                    "scope": [],
                    "next": "elegir una spec del sprint y declararla con implement_task",
                },
            )
        return summary

    if mode == "auto":
        if explicit is not None:
            summary = _summary(service, explicit)
            if _owns(explicit, session_id):
                return TaskResult.success(
                    {**base, "decision": "bound", "bound": summary,
                     "suggestion": _suggestion(summary, "bound")}
                )
            return TaskResult.success(
                {**base, "decision": "resume_available", "previous": summary,
                 "suggestion": _suggestion(summary, "resume_available")}
            )
        if mine is not None:
            summary = _summary(service, mine)
            return TaskResult.success(
                {**base, "decision": "bound", "bound": summary, "suggestion": _suggestion(summary, "bound")}
            )
        if closed_mine is not None:
            # No se miente diciendo "bound": el sprint previo terminó. Se
            # informa el cierre y la vía explícita para volver a trabajar.
            summary = _summary(service, closed_mine)
            return TaskResult.success(
                {
                    **base,
                    "decision": "no_work",
                    "previous": summary,
                    "suggestion": (
                        f"El sprint {summary['spring_id']} está cerrado (terminal). "
                        "Para trabajar de nuevo, creá un sprint nuevo con "
                        "session_bind mode=new: reposiciona el workflow y deja un "
                        "sprint vivo."
                    ),
                }
            )
        if active is not None:
            summary = _summary(service, active)
            return TaskResult.success(
                {
                    **base,
                    "decision": "resume_available",
                    "previous": summary,
                    "suggestion": _suggestion(summary, "resume_available"),
                }
            )
        last = service.last_session(project_id) if hasattr(service, "last_session") else None
        if last and last.get("session_id") != session_id and (last.get("goal") or last.get("spring_id")):
            summary = {
                "spring_id": last.get("spring_id") or "",
                "goal": last.get("goal") or "",
                "status": last.get("status") or "",
                "session_id": last.get("session_id") or "",
                "sessions": [],
                "tasks": [],
                "tasks_count": 0,
            }
            return TaskResult.success(
                {
                    **base,
                    "decision": "resume_available",
                    "previous": summary,
                    "last_session": last,
                    "suggestion": _suggestion(summary, "resume_available"),
                }
            )
        return TaskResult.success(
            {**base, "decision": "no_work", "suggestion": _suggestion({}, "no_work")}
        )

    if mode == "new":
        if mine is not None:
            summary = _summary(service, mine)
            return TaskResult.success(
                {**base, "decision": "bound", "created": False, "bound": summary,
                 "suggestion": _suggestion(summary, "bound")}
            )
        if explicit is not None:
            if str(explicit.get("status") or "") == "closed":
                return TaskResult.failure(
                    f"session_bind: {explicit.get('spring_id')} está cerrado "
                    "(terminal): no se puede reutilizar. Usá mode=new para uno nuevo."
                )
            # Unirse a un sprint existente (roadmap) en vez de crear/pisarlo.
            summary = _join(explicit)
            return TaskResult.success(
                {**base, "decision": "bound", "created": False, "continued": True, "bound": summary,
                 "suggestion": f"Esta sesión se sumó a {summary.get('spring_id')} (goal: {summary.get('goal') or ''})."},
            )
        # El sprint de la sesión se deriva de su id, pero una sesión puede tener
        # varios ciclos: el primero ocupa SPR-Sxxxx y los siguientes -2, -3…
        base_id = f"SPR-S{_short_hash(session_id)}"
        spring_id = base_id
        suffix = 2
        while hasattr(service, "get_spring") and service.get_spring(spring_id):
            spring_id = f"{base_id}-{suffix}"
            suffix += 1
        # Si el ciclo anterior cerró, el workflow puede estar en SPRINT.CLOSE.
        # Se lo reposiciona ANTES de crear: el auto-advance de CLOSE ejecuta
        # `close_sprint` y cerraría el sprint recién nacido (0 tareas → válido).
        if closed_mine is not None:
            reset = _di.reset_workflow_active(ctx.chain_dir)
            if not reset.get("ok"):
                return TaskResult.failure(
                    "session_bind: no se pudo reposicionar el workflow "
                    f"({reset.get('error')}); el sprint nuevo se cerraría solo. "
                    "Revisá el workflow y reintentá."
                )
        goal = objective or f"sesión {session_id}"
        context = {"session_ids": [session_id]}
        inherited_parent = parent or (str(active.get("session_id") or "") if active else "")
        if inherited_parent:
            context["parent_session_id"] = inherited_parent
        spring = service.create_spring(
            spring_id=spring_id,
            goal=goal,
            status="active",
            project_id=project_id,
            session_id=session_id,
            context=context,
        )
        summary = _summary(service, spring)
        service.open_session(
            session_id,
            project_id=project_id,
            parent_session_id=inherited_parent,
            spring_id=spring_id,
            goal=goal,
            context={"bound_by": "session_bind/new"},
        )
        if hasattr(service, "update_session_plan"):
            derived = derive(goal)
            service.update_session_plan(
                session_id,
                {
                    "goal": goal,
                    "acceptance": derived["acceptance_criteria"],
                    "task": "",
                    "scope": [],
                    "next": (
                        "declarar la tarea con implement_task"
                        if derived["acceptance_criteria"]
                        else "definir criterios de aceptación verificables"
                    ),
                },
            )
        return TaskResult.success(
            {**base, "decision": "bound", "created": True, "bound": summary,
             "suggestion": f"Sprint {spring_id} creado para esta sesión (goal: {goal})."},
        )

    # mode == "continue"
    target = explicit or mine or active
    if target is None:
        hint = (
            f" El sprint {closed_mine.get('spring_id')} está cerrado (terminal)."
            if closed_mine is not None
            else ""
        )
        return TaskResult.failure(
            "session_bind: no hay sprint para continuar; usá mode=new para crear uno."
            + hint
        )
    if str(target.get("status") or "") == "closed":
        return TaskResult.failure(
            f"session_bind: {target.get('spring_id')} está cerrado (terminal): "
            "no se continúa un sprint cerrado. Usá mode=new para uno nuevo."
        )
    summary = _join(target)
    return TaskResult.success(
        {**base, "decision": "bound", "created": False, "continued": True, "bound": summary,
         "suggestion": f"Esta sesión se sumó a {summary.get('spring_id')} (goal: {summary.get('goal') or ''})."},
    )


def session_open(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Alta idempotente de la sesión en el registro (ciclo de vida + jerarquía)."""
    session_id = str(data.get("session_id") or ctx.session_id or "").strip()
    if not session_id or session_id == "mcp":
        return TaskResult.failure("session_open: session_id requerido")
    service = _di.sprint_service(ctx.chain_dir)
    if service is None:
        return TaskResult.failure("session_open: sprint service no disponible")
    try:
        record = service.open_session(
            session_id,
            project_id=str(data.get("project_id") or ""),
            agent=str(data.get("agent") or ""),
            model=str(data.get("model") or ""),
            parent_session_id=str(data.get("parent_session_id") or ""),
            spring_id=str(data.get("spring_id") or ""),
            goal=str(data.get("goal") or ""),
            context=data.get("context") if isinstance(data.get("context"), dict) else None,
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult.failure(f"session_open: {type(exc).__name__}: {exc}")
    return TaskResult.success({"ok": True, "session": record})


def session_close(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    session_id = str(data.get("session_id") or ctx.session_id or "").strip()
    if not session_id or session_id == "mcp":
        return TaskResult.failure("session_close: session_id requerido")
    service = _di.sprint_service(ctx.chain_dir)
    if service is None:
        return TaskResult.failure("session_close: sprint service no disponible")

    # Obligación de memoria (P1.3). El motor la ejecuta en el sub-estado
    # KNOWLEDGE_UPDATE (`action: capture_knowledge`), pero esa rama solo corre
    # con `submit`: el agente trabaja con tools y nunca llega. Medido: 227
    # `kg_query` (lecturas) y 0 `capture_knowledge` (escrituras) en la traza.
    # Este es el cable: el cierre de sesión —que el plugin SÍ llama— captura el
    # hecho antes de cerrar. No rompe el cierre si falla: lo reporta.
    knowledge: dict[str, Any] = {}
    try:
        from fast_mcp.features.tasks.handlers.knowledge import capture_knowledge

        captured = capture_knowledge(
            ctx,
            {
                "session_id": session_id,
                "task_id": str(data.get("task_id") or ctx.task_id or ""),
            },
        )
        knowledge = dict(captured.data or {}) if captured.ok else {
            "ok": False,
            "error": captured.error or "",
        }
    except Exception as exc:  # noqa: BLE001 — el cierre no se cae por memoria
        knowledge = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    record = service.close_session(session_id, outcome=str(data.get("outcome") or ""))
    if record is None:
        return TaskResult.failure(f"session_close: sesión desconocida {session_id!r}")
    return TaskResult.success({"ok": True, "session": record, "knowledge": knowledge})


def session_list(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    service = _di.sprint_service(ctx.chain_dir)
    if service is None:
        return TaskResult.failure("session_list: sprint service no disponible")
    project_id = str(data.get("project_id") or "")
    try:
        limit = int(data.get("limit") or 0)
    except (TypeError, ValueError):
        limit = 0
    status = str(data.get("status") or "")
    sessions = service.list_sessions(status=status, project_id=project_id, limit=limit)
    return TaskResult.success(
        {
            "ok": True,
            "count": len(sessions),
            "sessions": sessions,
            "last": service.last_session(project_id),
            # Árbol completo del proyecto (ignora `limit`): es la vista de
            # jerarquía, no un paginado.
            "tree": service.session_tree(status=status, project_id=project_id),
        }
    )


def session_plan(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Plan de vuelo de la sesión (ADR-WALKER-080): get/set.

    Forma: {goal, acceptance[], task, scope[], next, updated_at}. Es el estado
    que el agente debe tener a la vista; vive en el registro para sobrevivir
    recargas del plugin.
    """
    session_id = str(data.get("session_id") or ctx.session_id or "").strip()
    if not session_id or session_id == "mcp":
        return TaskResult.failure("session_plan: session_id requerido")
    service = _di.sprint_service(ctx.chain_dir)
    if service is None:
        return TaskResult.failure("session_plan: sprint service no disponible")

    mode = str(data.get("mode") or "get").strip().lower()
    if mode not in ("get", "set", "add", "remove"):
        return TaskResult.failure("session_plan: mode debe ser get|set|add|remove")

    if mode == "get":
        record = service.get_session(session_id)
        if record is None:
            return TaskResult.failure(
                f"session_plan: sesión desconocida {session_id!r}; usá session_open o session_bind"
            )
        # Sesión existente sin plan = plan vacío (no es un error).
        return TaskResult.success({"plan": record.get("flight_plan") or {}})

    if mode in ("add", "remove"):
        # `set` obliga a reenviar el alcance completo (55 entradas hoy) para
        # agregar un archivo; el error se vuelve una lista ilegible. `add` y
        # `remove` tocan solo lo que cambió.
        current = service.get_session(session_id) or {}
        scope = list((current.get("flight_plan") or {}).get("scope") or [])
        items = data.get("scope")
        if isinstance(items, str):
            items = [items]
        if not isinstance(items, list) and data.get("path"):
            items = [data["path"]]
        if not isinstance(items, list) or not items:
            return TaskResult.failure(f"session_plan: mode={mode} requiere 'scope' o 'path'")
        wanted = [str(x) for x in items]
        if mode == "add":
            scope = scope + [x for x in wanted if x not in scope]
        else:
            drop = set(wanted)
            scope = [x for x in scope if x not in drop]
        plan = service.update_session_plan(session_id, {"scope": scope})
        if plan is None:
            return TaskResult.failure(
                f"session_plan: sesión desconocida {session_id!r}; usá session_open o session_bind"
            )
        return TaskResult.success({"plan": plan, "scope_size": len(scope)})

    patch: dict[str, Any] = {}
    for key in ("goal", "task", "next", "project"):
        if data.get(key) is not None:
            patch[key] = str(data[key])
    if isinstance(data.get("acceptance"), list):
        patch["acceptance"] = [str(x) for x in data["acceptance"]]
    if isinstance(data.get("scope"), list):
        patch["scope"] = [str(x) for x in data["scope"]]
    if not patch:
        return TaskResult.failure("session_plan: nada para actualizar")

    if patch.get("goal") and not patch.get("acceptance"):
        derived = derive(patch["goal"])
        patch["acceptance"] = derived["acceptance_criteria"]
        if not patch["acceptance"]:
            patch["next"] = patch.get("next") or "definir criterios de aceptación verificables"

    plan = service.update_session_plan(session_id, patch)
    if plan is None:
        return TaskResult.failure(
            f"session_plan: sesión desconocida {session_id!r}; usá session_open o session_bind"
        )
    return TaskResult.success({"plan": plan})


def spec_start(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Toma una spec existente (p. ej. del sprint del roadmap) y la inicia.

    Resuelve el sprint por `spring_id` o por la sesión, marca la spec
    `in_progress`, le une el `task_context` (task_id/session_id) y actualiza el
    plan de la sesión con la tarea y el alcance de la spec.
    """
    session_id = str(data.get("session_id") or ctx.session_id or "").strip()
    spec_id = str(data.get("spec_id") or "").strip()
    spring_id = str(data.get("spring_id") or data.get("sprint_id") or "").strip()
    if not spec_id:
        return TaskResult.failure("spec_start: spec_id requerido (ej. SPEC-001)")

    service = _di.sprint_service(ctx.chain_dir)
    if service is None:
        return TaskResult.failure("spec_start: sprint service no disponible")

    if not spring_id and session_id and hasattr(service, "get_session"):
        record = service.get_session(session_id) or {}
        spring_id = str(record.get("spring_id") or "")
    if not spring_id:
        return TaskResult.failure("spec_start: spring_id o sesión con sprint ligado")

    try:
        spec = service.get_spec(spring_id, spec_id)
    except Exception as exc:  # noqa: BLE001
        return TaskResult.failure(f"spec_start: {type(exc).__name__}: {exc}")
    if spec is None:
        return TaskResult.failure(f"spec_start: spec desconocida {spec_id!r} en {spring_id}")

    # La spec ES la tarea: se ignora el `task_id` que inyecta el runner desde el
    # workflow (medido: `snapshot-diff` pisaba el id de la spec).
    task_id = spec_id
    files = data.get("files") if isinstance(data.get("files"), list) else (spec.get("files") or [])
    files = [str(f) for f in (files or [])]
    updates = {
        "status": "in_progress",
        "task_context": {
            **(spec.get("task_context") or {}),
            "task_id": task_id,
            "session_id": session_id or "",
        },
    }
    if files:
        updates["files"] = files
    try:
        updated = service.update_spec(spring_id, spec_id, updates)
    except Exception as exc:  # noqa: BLE001
        return TaskResult.failure(f"spec_start: {type(exc).__name__}: {exc}")

    if session_id and hasattr(service, "update_session_plan"):
        service.update_session_plan(
            session_id,
            {"task": task_id, "scope": files, "next": "implementar y verificar"},
        )
    # El delta ya sabe la tarea, pero el ESTADO del workflow seguía con la
    # anterior: el diseño de la nueva spec se armaba con los datos de la vieja
    # (medido 2026-11-18: arrancó SPEC-001 y el prompt mostraba SPEC-002). El
    # recorder es el mismo puente que usa `implement_task` (R1: lo inyecta el
    # composition root).
    recorded = False
    try:
        recorded = bool(_di.record_task(ctx.chain_dir, task_id))
    except Exception as exc:  # noqa: BLE001 — no romper spec_start por esto
        logger.debug("spec_start: no se pudo recordar la tarea (%s)", exc)

    return TaskResult.success(
        {
            "ok": True,
            "spring_id": spring_id,
            "spec_id": spec_id,
            "task_id": task_id,
            "files": files,
            "spec": updated,
            "task_recorded": recorded,
        }
    )


def register(registry) -> None:
    registry.register(
        "session_open",
        session_open,
        metadata={"description": "Register/open a chat session", "tags": ["session"], "scope": "internal"},
    )
    registry.register(
        "session_close",
        session_close,
        metadata={"description": "Close a chat session", "tags": ["session"], "scope": "internal"},
    )
    registry.register(
        "session_list",
        session_list,
        metadata={"description": "List chat sessions", "tags": ["session"], "scope": "internal"},
    )
    registry.register(
        "session_plan",
        session_plan,
        metadata={"description": "Get/set the session flight plan", "tags": ["session", "focus"], "scope": "internal"},
    )
    registry.register(
        "spec_start",
        spec_start,
        metadata={"description": "Start an existing spec (status + session link)", "tags": ["session", "spec"], "scope": "internal"},
    )
    registry.register(
        "session_bind",
        session_bind,
        metadata={
            "description": "Bind a chat session to a sprint (delta-sprint)",
            "tags": ["session", "continuity"],
            "scope": "internal",
        },
    )
