"""handlers/knowledge.py — `capture_knowledge`: cierre de sesión al CKG.

El workflow tiene nodos `KNOWLEDGE_CAPTURE` / `KNOWLEDGE_UPDATE` que esperan que
el AGENTE documente. Medido: en sesiones reales el CKG queda vacío, porque el
agente no lo hace.

Esta acción lo corre el MOTOR: deriva de la sesión (tarea declarada + traza de
esa sesión) un hecho verificable y lo persiste con `session_id`, así el join
`session_context` encuentra algo en la capa CKG.
"""
from __future__ import annotations

from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult
from fast_mcp.features.tasks.handlers import _di


def _fact_for(ctx: ExecutionContext, data: dict[str, Any]) -> dict | None:
    session_id = str(data.get("session_id", "") or "").strip()
    task_id = str(data.get("task_id", "") or "").strip()
    spec = _di.current_spec(ctx.chain_dir or "", task_id=task_id)

    if spec:
        # La spec es la SSOT del task_id: el caller puede pasar un rótulo
        # arbitrario (o "mcp"), pero el nodo debe quedar identificado con la
        # tarea real para que la retención (supersede por tarea) funcione.
        spec_task = str((spec.get("task_context") or {}).get("task_id", "") or "")
        task_id = spec_task or task_id
        session_id = session_id or str((spec.get("task_context") or {}).get("session_id", "") or "")
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

    fact = _fact_for(ctx, data)
    if fact is None:
        return TaskResult.failure(
            "capture_knowledge: no hay sesión ni tarea declarada que documentar"
        )

    task_id = str((fact.get("metadata") or {}).get("task_id") or data.get("task_id", "") or "")
    supersedes = _supersedes_for(ctx.chain_dir, fact, task_id)
    if supersedes:
        fact["supersedes"] = supersedes

    result = _di.save_knowledge(
        ctx.chain_dir,
        [fact],
        str(fact["metadata"]["task_id"]),
        str(data.get("session_id", "") or fact["metadata"]["session_id"]),
    )
    if result is None:
        return TaskResult.failure("capture_knowledge: writer del CKG no inyectado")

    return TaskResult.success(
        data={
            "fact": fact["id"],
            "statement": fact["statement"][:200],
            "superseded": len(supersedes),
        }
    )


def capture_session_fact(
    ctx: ExecutionContext, *, session_id: str = "", task_id: str = ""
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
        result = capture_knowledge(
            ctx, {"session_id": sid, "task_id": task_id or ""}
        )
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
