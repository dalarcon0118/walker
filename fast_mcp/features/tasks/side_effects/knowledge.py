"""side_effects/knowledge.py — efectos de conocimiento (CKG).

`documenting_knowledge`, `update_knowledge_status`, `remove_knowledge`
(compensación) y `session_reflect` (consolidación, ADR-084 F2).

Contrato:
  * `documenting_knowledge` es idempotente: si la acción del nodo ya guardó un
    nodo (`node_id` en los campos) se salta; si no, persiste la decisión del
    nodo como nodo de CKG.
  * `update_knowledge_status` mueve `spring.ckg_status`
    (`pending_update` / `up_to_date`), que es el campo real del sprint.
  * `remove_knowledge` borra por `task_id` (compensación de la saga).
  * `session_reflect` consolida la sesión en el CKG al cerrar: garantiza un
    `task_ledger` ligado a la sesión y a la tarea, construyéndolo desde el trace
    si el agente no lo capturó. Idempotente por (session, task).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_PENDING = "pending_update"
_UP_TO_DATE = "up_to_date"


def _state_fields(state: Any, wanted: tuple[str, ...] = ()) -> dict[str, Any]:
    """Campos de los sub-estados del nodo, del más nuevo al más viejo."""
    if state is None:
        return {}
    sub_data = getattr(state, "sub_state_data", None) or (
        state.get("sub_state_data") if isinstance(state, dict) else None
    )
    if not isinstance(sub_data, dict):
        return {}
    fallback: dict[str, Any] = {}
    for key in reversed(list(sub_data.keys())):
        entry = sub_data[key]
        fields = getattr(entry, "fields", None)
        if fields is None and isinstance(entry, dict):
            fields = entry.get("fields", entry)
        if not isinstance(fields, dict) or not fields:
            continue
        if wanted and any(fields.get(name) for name in wanted):
            return fields
        if not fallback:
            fallback = fields
    return {} if wanted else fallback


def _save_nodes(chain_dir: str, nodes: list[dict], task_id: str, session_id: str) -> dict[str, Any]:
    from fast_mcp.composition import ckg_save_nodes

    return ckg_save_nodes(chain_dir, nodes, task_id=task_id, session_id=session_id)


def _applies_to(value: Any) -> list[str]:
    """Normaliza `applies_to` (lista, string con comas o `files`)."""
    if isinstance(value, str):
        value = [part.strip() for part in value.replace(";", ",").split(",")]
    if not isinstance(value, (list, tuple, set)):
        return []
    seen: list[str] = []
    for item in value:
        text = str(item or "").strip().lstrip("./")
        if text and text not in seen:
            seen.append(text)
    return seen


def documenting_knowledge(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    from ._context import session_id_of, task_id_of

    params = params or {}
    task_id = task_id_of(params, kwargs)
    session_id = session_id_of(params, kwargs)

    nodes = params.get("nodes")
    if isinstance(nodes, list) and nodes:
        # ADR-084 F2a: una decisión declara a qué archivos/áreas aplica. Sin
        # esto, `brief` solo puede ordenar por texto y cita genéricos. Se
        # acepta `applies_to` explícito en el nodo o, si falta, los `files`
        # de la tarea (o del params).
        declared = _applies_to(params.get("applies_to") or params.get("files") or [])
        for node in nodes:
            if not isinstance(node, dict):
                continue
            meta = node.setdefault("metadata", {})
            if isinstance(meta, dict) and not meta.get("applies_to"):
                meta["applies_to"] = _applies_to(
                    node.get("applies_to") or node.get("files") or declared
                )
        out = _save_nodes(chain_dir, nodes, task_id, session_id)
        out.setdefault("source", "params")
        return out

    fields = _state_fields(
        kwargs.get("state") or kwargs.get("workflow_state"),
        ("node_id", "nodes_saved", "decision", "statement"),
    )
    if fields.get("node_id") or fields.get("nodes_saved"):
        return {
            "ok": True,
            "skipped": True,
            "reason": "el nodo ya persistió conocimiento",
            "node_id": fields.get("node_id", ""),
        }

    statement = str(
        params.get("statement") or params.get("summary") or fields.get("decision") or ""
    ).strip()
    if not statement:
        return {
            "ok": True,
            "skipped": True,
            "reason": "sin decisión ni nodos que persistir",
        }

    if task_id:
        try:
            from ._context import ckg as _ckg

            graph = _ckg(chain_dir)
            if graph is not None:
                existing = graph.query({"type": "decision", "metadata.spec_id": task_id})
                if existing:
                    return {
                        "ok": True,
                        "skipped": True,
                        "reason": "decisión ya registrada para la tarea",
                        "node_id": existing[0].get("_id", ""),
                    }
        except Exception as exc:  # noqa: BLE001
            logger.debug("documenting_knowledge: query falló (%s)", exc)

    node = {
        "label": statement[:200],
        "description": str(fields.get("evidence") or params.get("rationale") or ""),
        "type": str(params.get("type") or "decision"),
        "metadata": {
            "spec_id": task_id,
            "task_id": task_id,
            "generated_from": f"NODE:{params.get('sub_state') or ''}",
        },
    }
    out = _save_nodes(chain_dir, [node], task_id, session_id)
    out["source"] = "state-decision"
    return out


def update_knowledge_status(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Mueve spring.ckg_status (pending_update / up_to_date)."""
    from ._context import session_id_of, task_id_of

    params = params or {}
    task_id = task_id_of(params, kwargs)
    session_id = session_id_of(params, kwargs)
    event = str(params.get("event") or "")
    # El status se deriva del evento, no se asume. El test (y el workflow) lo
    # declara explícitamente: implementation_start → pending_update; captura/retro
    # → up_to_date.
    _EVENT_STATUS = {
        "implementation_start": _PENDING,
        "knowledge_captured": _UP_TO_DATE,
        "sprint_retro_completed": _UP_TO_DATE,
    }
    if event in _EVENT_STATUS:
        status = _EVENT_STATUS[event]
    else:
        status = str(params.get("status") or params.get("ckg_status") or _UP_TO_DATE)
    # Idempotencia: si el spring ya tiene el status objetivo, no se escribe.
    svc = kwargs.get("sprint_service")
    spring_id = kwargs.get("spring_id")
    if svc is not None and spring_id is not None and hasattr(svc, "springs"):
        spring = svc.springs.get(spring_id) if isinstance(svc.springs, dict) else None
        if isinstance(spring, dict) and spring.get("ckg_status") == status:
            return {"ok": True, "idempotent": True, "status": status,
                    "task_id": task_id, "session_id": session_id}
    # El servicio real (si existe) se actualiza.
    try:
        from fast_mcp.composition import build_sprint_service

        service = build_sprint_service(chain_dir)
        if service is not None and hasattr(service, "update_ckg_status"):
            service.update_ckg_status(task_id, status)
    except Exception as exc:  # noqa: BLE001
        logger.debug("update_knowledge_status: %s", exc)
    if svc is not None and spring_id is not None and hasattr(svc, "springs"):
        if isinstance(svc.springs, dict) and spring_id in svc.springs:
            svc.springs[spring_id]["ckg_status"] = status
    return {"ok": True, "status": status, "task_id": task_id, "session_id": session_id}


def remove_knowledge(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Compensación: borra nodos del CKG por task_id."""
    from ._context import task_id_of

    params = params or {}
    task_id = task_id_of(params, kwargs)
    if not task_id:
        return {"ok": True, "skipped": True, "reason": "sin task_id"}
    try:
        from fast_mcp.composition import ckg_delete_by_metadata

        ckg_delete_by_metadata(chain_dir, "task_id", task_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("remove_knowledge: %s", exc)
    return {"ok": True, "task_id": task_id}


def session_reflect(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Consolida la sesión en el CKG (ADR-084 F2).

    Garantiza un `task_ledger` ligado a la sesión y a la tarea, construyéndolo
    desde el trace si el agente no lo capturó. Idempotente por (session, task).
    No bloquea el cierre si falla.
    """
    from ._context import session_id_of, task_id_of

    params = params or {}
    task_id = task_id_of(params, kwargs)
    session_id = session_id_of(params, kwargs)
    if not session_id:
        return {"ok": True, "skipped": True, "reason": "sin session_id"}

    # Si el caller pasó un ledger completo, usarlo; si no, skeleton del trace.
    ledger = params.get("ledger")
    if ledger == "auto" or not isinstance(ledger, dict):
        try:
            from fast_mcp.features.tasks.handlers.capture import build_ledger_skeleton

            skeleton = build_ledger_skeleton(chain_dir, session_id, task_id)
            if isinstance(ledger, dict):
                from fast_mcp.features.tasks.handlers.capture import _merge_ledger

                ledger = _merge_ledger(skeleton, ledger)
            else:
                ledger = skeleton
        except Exception as exc:  # noqa: BLE001
            logger.debug("session_reflect: skeleton falló (%s)", exc)
            ledger = {"problem": "", "hypotheses": [], "files": [], "evidence": []}

    # Idempotencia: si ya hay un task_ledger para (session, task), no duplicar.
    try:
        from fast_mcp.composition import ckg_for

        ckg = ckg_for(chain_dir)
        if ckg is not None and task_id and session_id:
            existing = ckg.query({"type": "task-ledger", "metadata.session_id": session_id,
                                  "metadata.task_id": task_id}) or []
            if existing:
                return {"ok": True, "skipped": True,
                        "reason": "task_ledger ya registrado para la tarea",
                        "node_id": existing[0].get("_id", "")}
    except Exception as exc:  # noqa: BLE001
        logger.debug("session_reflect: idempotencia falló (%s)", exc)

    from fast_mcp.features.tasks.handlers.capture import _normalize_ledger

    normalized = _normalize_ledger(ledger)
    if normalized is None:
        return {"ok": True, "skipped": True, "reason": "sin contenido capturable"}

    node_id = f"task_ledger-{session_id}-{task_id or 'anon'}"
    node = {
        "label": (normalized.get("problem") or task_id or "task_ledger")[:200],
        "description": json.dumps(normalized, ensure_ascii=False),
        "type": "task_ledger",
        "tags": ["task_ledger", "session", "reflection", "consolidation"],
        "metadata": {
            "session_id": session_id,
            "task_id": task_id or "",
            "spec_id": task_id or "",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "applies_to": [],
            "consolidated": True,
        },
    }
    try:
        out = _save_nodes(chain_dir, [node], task_id, session_id)
        if not out.get("ok"):
            return {"ok": False, "error": out.get("error", "fallo al guardar")}
        return {"ok": True, "saved": True, "node_id": node_id}
    except Exception as exc:  # noqa: BLE001
        logger.debug("session_reflect: persistencia falló (%s)", exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def register(registry) -> None:
    """Register side effects."""
    registry.register("documenting_knowledge", documenting_knowledge)
    registry.register("session_reflect", session_reflect)
    registry.register("update_knowledge_status", update_knowledge_status)
    registry.register("remove_knowledge", remove_knowledge)