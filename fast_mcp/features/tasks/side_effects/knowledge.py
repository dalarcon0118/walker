"""side_effects/knowledge.py — efectos de conocimiento (CKG).

`documenting_knowledge`, `update_knowledge_status` y `remove_knowledge`
(compensación) estaban declarados por el grafo y no existían. Sin ellos el
CKG no recibía nada del workflow: `capture_decision` solo devuelve un dict,
no persiste.

Contrato:
  * `documenting_knowledge` es idempotente: si la acción del nodo ya guardó un
    nodo (`node_id` en los campos) se salta; si no, persiste la decisión del
    nodo como nodo de CKG.
  * `update_knowledge_status` mueve `spring.ckg_status`
    (`pending_update` / `up_to_date`), que es el campo real del sprint.
  * `remove_knowledge` borra por `task_id` (compensación de la saga).
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_PENDING = "pending_update"
_UP_TO_DATE = "up_to_date"


def _state_fields(state: Any) -> dict[str, Any]:
    """Campos registrados en los sub-estados del nodo actual, del más nuevo al más viejo."""
    if state is None:
        return {}
    sub_data = getattr(state, "sub_state_data", None) or (
        state.get("sub_state_data") if isinstance(state, dict) else None
    )
    if not isinstance(sub_data, dict):
        return {}
    for key in reversed(list(sub_data.keys())):
        entry = sub_data[key]
        fields = getattr(entry, "fields", None)
        if fields is None and isinstance(entry, dict):
            fields = entry.get("fields", entry)
        if isinstance(fields, dict) and fields:
            return fields
    return {}


def _save_nodes(chain_dir: str, nodes: list[dict], task_id: str, session_id: str) -> dict[str, Any]:
    from fast_mcp.composition import ckg_save_nodes

    return ckg_save_nodes(chain_dir, nodes, task_id=task_id, session_id=session_id)


def documenting_knowledge(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    from ._context import session_id_of, task_id_of

    params = params or {}
    task_id = task_id_of(params, kwargs)
    session_id = session_id_of(params, kwargs)

    nodes = params.get("nodes")
    if isinstance(nodes, list) and nodes:
        out = _save_nodes(chain_dir, nodes, task_id, session_id)
        out.setdefault("source", "params")
        return out

    # La acción del nodo (DESIGN.CKG_SAVE) ya persistió: no duplicar.
    fields = _state_fields(kwargs.get("state") or kwargs.get("workflow_state"))
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

    # Idempotencia por tarea: una decisión por spec es suficiente.
    if task_id:
        try:
            from ._context import ckg

            graph = ckg(chain_dir)
            if graph is not None:
                existing = graph.query({"type": "decision", "metadata.spec_id": task_id})
                if existing:
                    return {
                        "ok": True,
                        "skipped": True,
                        "reason": "decisión ya registrada para la tarea",
                        "node_id": existing[0].get("_id", ""),
                    }
        except Exception as exc:  # noqa: BLE001 — la consulta no debe bloquear
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


def _status_for(event: str) -> str:
    """Evento → estado del CKG. Explícito antes que heurístico."""
    e = event.strip().lower()
    if e in ("implementation_start", "implementation_started"):
        return _PENDING
    if e in ("knowledge_captured", "capture_done", "knowledge_updated"):
        return _UP_TO_DATE
    if "start" in e:
        return _PENDING
    return _UP_TO_DATE


def update_knowledge_status(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    from ._context import sprint_service, spring_id_of

    params = params or {}
    event = str(params.get("event") or "")
    status = _status_for(event) if event else str(params.get("status") or _UP_TO_DATE)

    service = sprint_service(chain_dir, params, kwargs)
    if service is None:
        return {"ok": True, "skipped": True, "reason": "sin sprint service", "status": status}
    spring_id = spring_id_of(service, chain_dir, params, kwargs)
    if not spring_id:
        return {"ok": True, "skipped": True, "reason": "sin sprint", "status": status}

    previous = ""
    try:
        spring = service.get_spring(spring_id) or {}
        previous = str(spring.get("ckg_status") or "")
    except Exception:  # noqa: BLE001
        spring = {}

    if previous == status:
        return {
            "ok": True,
            "status": status,
            "spring_id": spring_id,
            "idempotent": True,
        }
    try:
        service.update_spring(spring_id, {"ckg_status": status})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "status": status}
    return {
        "ok": True,
        "status": status,
        "previous": previous,
        "spring_id": spring_id,
        "event": event,
    }


def remove_knowledge(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Compensación: elimina el conocimiento escrito para la tarea."""
    from ._context import task_id_of

    params = params or {}
    task_id = task_id_of(params, kwargs)
    if not task_id:
        return {"ok": True, "skipped": True, "reason": "sin task_id"}
    try:
        from fast_mcp.composition import ckg_delete_by_metadata

        result = ckg_delete_by_metadata(chain_dir, "task_id", task_id)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": bool(result.get("ok", True)), "task_id": task_id, "result": result}


def register(registry) -> None:
    registry.register("documenting_knowledge", documenting_knowledge)
    registry.register("update_knowledge_status", update_knowledge_status)
    registry.register("remove_knowledge", remove_knowledge)
