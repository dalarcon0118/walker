"""handlers/knowledge.py — `capture_knowledge`: cierre de sesión al CKG.

El workflow tiene nodos `KNOWLEDGE_CAPTURE` / `KNOWLEDGE_UPDATE` que esperan que
el AGENTE documente. Medido: en sesiones reales el CKG queda vacío, porque el
agente no lo hace.

Esta acción lo corre el MOTOR: deriva de la sesión (tarea declarada + traza de
esa sesión) un hecho verificable y lo persiste con `session_id`, así el join
`session_context` encuentra algo en la capa CKG.
"""
from __future__ import annotations

import re
from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult
from fast_mcp.features.tasks.handlers import _di


def _session_for_task(chain_dir: str, task_id: str) -> str:
    """Sesión activa cuyo plan de vuelo declara esta tarea.

    Solo el plan. Adivinar "la única sesión activa" parecía razonable y mezcló
    trazas ajenas (medido 2026-11-20: el harness y esta sesión corriendo a la
    vez dejaron 13 nodos `session` con la tarea de acá y la sesión del harness).
    Sin sesión declarada y sin spec, la captura se reporta como fallida.
    """
    service = _di.sprint_service(chain_dir)
    if service is None or not task_id or not hasattr(service, "list_sessions"):
        return ""
    try:
        sessions = service.list_sessions(status="active") or []
    except Exception:  # noqa: BLE001 — sin sesión se sigue con lo que haya
        return ""
    for session in sessions:
        plan = session.get("flight_plan") or {}
        if str(plan.get("task") or "") == task_id:
            return str(session.get("session_id") or "")
    return ""


def _plan_task(chain_dir: str, session_id: str) -> str:
    if not session_id:
        return ""
    try:
        service = _di.sprint_service(chain_dir)
    except Exception:  # noqa: BLE001 — sin proyecto no hay plan, no es fatal
        return ""
    if service is None or not hasattr(service, "get_session_plan"):
        return ""
    try:
        return str((service.get_session_plan(session_id) or {}).get("task") or "")
    except Exception:  # noqa: BLE001
        return ""


def _resolve_spec(
    chain_dir: str, session_id: str, task_id: str, spring_id: str = ""
) -> dict | None:
    """Spec de la tarea, sin heredar la identidad de otra (P1.6).

    Orden: id exacto que nombró el caller → tarea del plan (solo si el caller no
    nombró tarea) → heurística `in_progress` (solo cuando no hay ninguna tarea
    declarada). Antes, cualquier id desconocido caía en la última spec
    `in_progress` del sprint y el hecho se guardaba con la identidad de otra
    tarea (medido: `prepare-seeds-manifest` como `skill-token-memory-health`).

    `spring_id` acota la búsqueda: los ids `SPEC-NNN` se repiten en cada sprint
    y resolverlos sin alcance firmaba el hecho con la tarea de otro sprint
    (medido 2026-11-20: SPEC-012 de SPR-INTEG capturó `no-mcp-v2`).
    """
    def _lookup(tid: str) -> dict | None:
        # `spring_id` sólo viaja cuando existe: los dobles de test que
        # reemplazan `current_spec` conservan su firma histórica.
        if spring_id:
            return _di.current_spec(
                chain_dir, task_id=tid, exact=True, spring_id=spring_id
            )
        return _di.current_spec(chain_dir, task_id=tid, exact=True)

    if task_id:
        return _lookup(task_id)
    plan_task = _plan_task(chain_dir, session_id)
    if plan_task:
        return _lookup(plan_task)
    if spring_id:
        return _di.current_spec(chain_dir, spring_id=spring_id)
    return _di.current_spec(chain_dir)


def _capture_scope_error(chain_dir: str, data: dict) -> str:
    """Motivo por el que la captura no puede resolver la identidad, o ''.

    Un `SPEC-NNN` es local a su sprint. Sin `spring_id` (ni sesión ligada) la
    resolución sería ambiguua; se declara en vez de elegir el primero del
    índice y firmar el hecho con otra tarea.
    """
    task_id = str(data.get("task_id") or "").strip()
    if not task_id or str(data.get("spring_id") or "").strip():
        return ""
    session_id = str(data.get("session_id") or "").strip()
    if session_id and _di.session_spring(chain_dir, session_id):
        return ""
    if re.fullmatch(r"SPEC-\d+", task_id):
        return (
            f"{task_id!r} es un id de spec local a un sprint (se repite en cada "
            "sprint); pasá spring_id o session_id"
        )
    try:
        service = _di.sprint_service(chain_dir)
        all_springs = (service.list_springs() or []) if service is not None else []
    except Exception:  # noqa: BLE001 — sin listado no se puede afirmar ambigüedad
        return ""
    springs: list[str] = []
    for spring in all_springs:
        sid = str(spring.get("spring_id") or spring.get("id") or "")
        for spec in service.get_specs(sid) or []:
            if (
                str((spec.get("task_context") or {}).get("task_id") or "") == task_id
                or str(spec.get("spec_id") or "") == task_id
            ):
                if sid not in springs:
                    springs.append(sid)
                break
    if len(springs) > 1:
        return (
            f"{task_id!r} aparece en {len(springs)} sprints "
            f"({', '.join(springs[:3])}); pasá spring_id o session_id"
        )
    return ""


def _fact_for(ctx: ExecutionContext, data: dict[str, Any]) -> dict | None:
    explicit_session = str(data.get("session_id", "") or "").strip()
    session_id = explicit_session
    spring_id = str(data.get("spring_id", "") or "").strip()
    task_id = str(data.get("task_id", "") or "").strip()
    if not spring_id and session_id:
        spring_id = _di.session_spring(ctx.chain_dir or "", session_id)
    if not session_id and task_id:
        session_id = _session_for_task(ctx.chain_dir or "", task_id)
    spec = _resolve_spec(ctx.chain_dir or "", session_id, task_id, spring_id)

    if spec:
        # La spec es la SSOT de la identidad: el caller puede pasar un rótulo
        # arbitrario, pero el nodo queda con la tarea real para que la retención
        # (supersede por tarea) funcione.
        tc = spec.get("task_context") or {}
        spec_task = str(tc.get("task_id") or spec.get("spec_id") or "")
        if spec_task:
            task_id = spec_task
        # La spec sabe a qué sesión pertenece la tarea. Una sesión resuelta por
        # el plan NO puede pisarla (medido: capturas con la sesión del harness).
        if not explicit_session:
            session_id = str(tc.get("session_id") or "") or session_id
        objective = str(spec.get("objective", "") or "")
        files = [f for f in (spec.get("files") or []) if isinstance(f, str)]
    else:
        objective = ""
        files = []

    events = _di.session_events(ctx.chain_dir or "", session_id) if session_id else []

    for event in events:
        for f in (event.files or []):
            if f not in files:
                files.append(f)

    if not objective and not events:
        return None

    statement = objective or f"sesión {session_id or '(sin id)'}"
    detail = []
    if files:
        detail.append(f"archivos: {', '.join(files[:6])}")
    if events:
        detail.append(f"{len(events)} evento(s): {', '.join(sorted({e.event_type for e in events}))[:160]}")
    if detail:
        statement = f"{statement} — " + "; ".join(detail)

    node_id = f"session-{(session_id or 'sin-id')[-12:]}-{(task_id or 'tarea')}"
    from datetime import datetime, timezone

    return {
        "id": node_id,
        "type": "session",
        "subject": task_id or session_id or "sesión",
        "statement": statement[:600],
        "rationale": f"Cierre automático de la sesión {session_id or '(sin id)'}",
        "scope": "session",
        "tags": ["session", "task", "auto"],
        "metadata": {
            "session_id": session_id,
            "task_id": task_id,
            "files": files[:20],
            "events": len(events),
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source": f"session:{session_id}" if session_id else "session",
        },
    }


#: Techo de nodos `session` activos por proyecto. La captura supersede los
#: viejos (misma tarea, o por encima del techo) en vez de acumular ruido.
MAX_SESSION_FACTS = 25


def _supersedes_for(chain_dir: str, fact: dict, task_id: str) -> list[str]:
    """Ids de nodos `session` que este hecho reemplaza.

    Dos políticas, en este orden:
      1. misma tarea: el hecho nuevo es la versión vigente; los viejos de esa
         tarea se superseden (el CKG no guarda dos verdades de la misma tarea).
      2. techo: se conservan los `MAX_SESSION_FACTS - 1` más recientes y se
         supersede el resto (crecimiento acotado, sin borrar historia).
    """
    nodes = _di.session_facts(chain_dir)

    def _id(n: dict) -> str:
        return str(n.get("_id") or n.get("id") or "")

    new_id = str(fact.get("id") or "")
    others = [n for n in nodes if _id(n) and _id(n) != new_id]
    same = [
        n for n in others
        if task_id and str((n.get("metadata") or {}).get("task_id") or "") == task_id
    ]
    same_ids = {_id(n) for n in same}
    rest = [n for n in others if _id(n) not in same_ids]

    def _ts(n: dict) -> str:
        meta = n.get("metadata") or {}
        return str(meta.get("captured_at") or n.get("updated_at") or n.get("created_at") or "")

    rest.sort(key=_ts, reverse=True)  # más recientes primero
    excess = rest[max(0, MAX_SESSION_FACTS - 1):]
    return sorted({_id(n) for n in (same + excess) if _id(n)})


def capture_knowledge(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Deriva un hecho de la sesión y lo guarda en el CKG."""
    if not ctx.chain_dir:
        return TaskResult.failure("capture_knowledge: sin chain_dir")
    scope_error = _capture_scope_error(ctx.chain_dir, data)
    if scope_error:
        return TaskResult.failure(f"capture_knowledge: {scope_error}")

    fact = _fact_for(ctx, data)
    if fact is None:
        return TaskResult.failure(
            "capture_knowledge: no hay evidencia de la tarea "
            f"{str(data.get('task_id') or data.get('session_id') or '(sin id)')!r} "
            "(ni spec ni traza de la sesión)"
        )

    task_id = str((fact.get("metadata") or {}).get("task_id") or data.get("task_id", "") or "")
    supersedes = _supersedes_for(ctx.chain_dir, fact, task_id)
    if supersedes:
        fact["supersedes"] = supersedes

    result = _di.save_knowledge(
        ctx.chain_dir,
        [fact],
        str(fact["metadata"]["task_id"]),
        str(fact["metadata"]["session_id"]),
    )
    if result is None:
        return TaskResult.failure("capture_knowledge: writer del CKG no inyectado")

    saved = result if isinstance(result, dict) else {}
    if saved.get("ok") is False:
        return TaskResult.failure(
            f"capture_knowledge: {saved.get('error') or 'writer del CKG falló'}"
        )
    # `superseded` es lo que el writer DEGRADÓ, no lo que se le pidió: el
    # contrato anterior reportaba 13 y dejaba los 13 activos (la página default
    # de `query` no los traía). Lo pedido y lo faltante quedan visibles.
    applied = saved.get("degraded")
    return TaskResult.success(
        data={
            "fact": fact["id"],
            "statement": fact["statement"][:200],
            "supersede_requested": len(supersedes),
            "superseded": len(applied) if isinstance(applied, list) else len(supersedes),
            "supersede_missing": saved.get("supersede_missing") or [],
        }
    )


def capture_session_fact(
    ctx: ExecutionContext, *, session_id: str = "", task_id: str = "", spring_id: str = ""
) -> dict:
    """Captura el hecho de la sesión como EFECTO de una tool (P1.3).

    Las tools que el agente usa de verdad (`run_tests` verde, `spec_status`
    completed, `session_close`) llaman acá. No propaga excepciones ni convierte
    una tool exitosa en fallo: devuelve `{"ok": True, ...}` o
    `{"ok": False, "error": ...}` para que el llamador lo reporte (visible,
    nunca silencioso).
    """
    try:
        # "mcp" es el placeholder del canal, no una sesión: si se pasa tal cual,
        # la captura crea un nodo con session_id="mcp" en vez de usar la sesión
        # real de la spec (medido: dos nodos para la misma tarea).
        sid = str(session_id or "").strip()
        if sid == "mcp":
            sid = ""
        payload = {"session_id": sid, "task_id": task_id or ""}
        if spring_id:
            # Sólo viaja cuando existe: los dobles existentes conservan la firma.
            payload["spring_id"] = str(spring_id)
        result = capture_knowledge(ctx, payload)
    except Exception as exc:  # noqa: BLE001 — la tool no se cae por memoria
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if result.ok:
        return dict(result.data or {})
    return {"ok": False, "error": result.error or ""}


def register(registry) -> None:
    registry.register("capture_knowledge", capture_knowledge, metadata={
        "description": "KNOWLEDGE CAPTURE: cierra la sesión escribiendo un hecho en el CKG",
        "tags": ["knowledge", "session"],
        "scope": "internal",
    })
