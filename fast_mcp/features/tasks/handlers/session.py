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
from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult
from fast_mcp.features.tasks.handlers import _di


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
        return (
            f"Esta sesión ya pertenece a {summary['spring_id']} (goal: {goal}, "
            f"{tasks} tarea(s)). No crees otro sprint: seguí declarando con implement_task."
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

    service = _di.sprint_service(ctx.chain_dir)
    if service is None:
        return TaskResult.failure("session_bind: sprint service no disponible para este proyecto")

    springs = service.list_springs() or []
    mine = next((s for s in springs if _owns(s, session_id)), None)
    active = next((s for s in springs if str(s.get("status") or "") == "active"), None)
    if active is None and springs:
        active = springs[-1]

    base = {
        "mode": mode,
        "session_id": session_id,
        "project_id": project_id,
    }

    if mode == "auto":
        if mine is not None:
            summary = _summary(service, mine)
            return TaskResult.success(
                {**base, "decision": "bound", "bound": summary, "suggestion": _suggestion(summary, "bound")}
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
            {
                **base,
                "decision": "no_work",
                "suggestion": _suggestion({}, "no_work"),
            }
        )

    if mode == "new":
        if mine is not None:
            summary = _summary(service, mine)
            return TaskResult.success(
                {**base, "decision": "bound", "created": False, "bound": summary,
                 "suggestion": _suggestion(summary, "bound")}
            )
        spring_id = f"SPR-S{_short_hash(session_id)}"
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
        return TaskResult.success(
            {**base, "decision": "bound", "created": True, "bound": summary,
             "suggestion": f"Sprint {spring_id} creado para esta sesión (goal: {goal})."},
        )

    # mode == "continue"
    target = mine or active
    if target is None:
        return TaskResult.failure(
            "session_bind: no hay sprint para continuar; usá mode=new para crear uno"
        )
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
        context={"bound_by": "session_bind/continue"},
    )
    return TaskResult.success(
        {**base, "decision": "bound", "created": False, "continued": True, "bound": summary,
         "suggestion": f"Esta sesión se sumó a {spring_id} (goal: {summary.get('goal') or ''})."},
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
    record = service.close_session(session_id, outcome=str(data.get("outcome") or ""))
    if record is None:
        return TaskResult.failure(f"session_close: sesión desconocida {session_id!r}")
    return TaskResult.success({"ok": True, "session": record})


def session_list(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    service = _di.sprint_service(ctx.chain_dir)
    if service is None:
        return TaskResult.failure("session_list: sprint service no disponible")
    project_id = str(data.get("project_id") or "")
    try:
        limit = int(data.get("limit") or 0)
    except (TypeError, ValueError):
        limit = 0
    sessions = service.list_sessions(
        status=str(data.get("status") or ""), project_id=project_id, limit=limit
    )
    return TaskResult.success(
        {
            "ok": True,
            "count": len(sessions),
            "sessions": sessions,
            "last": service.last_session(project_id),
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
        "session_bind",
        session_bind,
        metadata={
            "description": "Bind a chat session to a sprint (delta-sprint)",
            "tags": ["session", "continuity"],
            "scope": "internal",
        },
    )
