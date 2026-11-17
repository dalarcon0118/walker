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
        task_id = task_id or str((spec.get("task_context") or {}).get("task_id", "") or "")
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
            "source": f"session:{session_id}" if session_id else "session",
        },
    }


def capture_knowledge(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Deriva un hecho de la sesión y lo guarda en el CKG."""
    if not ctx.chain_dir:
        return TaskResult.failure("capture_knowledge: sin chain_dir")

    fact = _fact_for(ctx, data)
    if fact is None:
        return TaskResult.failure(
            "capture_knowledge: no hay sesión ni tarea declarada que documentar"
        )

    result = _di.save_knowledge(
        ctx.chain_dir,
        [fact],
        str(data.get("task_id", "") or fact["metadata"]["task_id"]),
        str(data.get("session_id", "") or fact["metadata"]["session_id"]),
    )
    if result is None:
        return TaskResult.failure("capture_knowledge: writer del CKG no inyectado")

    return TaskResult.success(data={"fact": fact["id"], "statement": fact["statement"][:200]})


def register(registry) -> None:
    registry.register("capture_knowledge", capture_knowledge, metadata={
        "description": "KNOWLEDGE CAPTURE: cierra la sesión escribiendo un hecho en el CKG",
        "tags": ["knowledge", "session"],
        "scope": "internal",
    })
