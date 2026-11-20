"""side_effects/sprint.py — efectos del nodo SPRINT.

Seis efectos declarados por el grafo sin implementación:

  * `sprint_status`       — mueve el sprint solo si la decisión coincide.
  * `advance_sprint_task` — avanza el cursor del sprint a la tarea siguiente.
  * `archive_sprint`      — cierra el sprint al terminar (idempotente).
  * `create_adr`          — ADR en el CKG cuando la spec se aprueba como decisión.
  * `index_sprint_tasks`  — verifica que la tarea del sprint sea consultable.
  * `rebuild_sprint_index`— verifica todas las tareas al cerrar.

Sobre el índice: en esta arquitectura el **DeltaGraph es el SSOT** y
`search_memories` lee las specs directamente de él; no hay un índice paralelo
que reconstruir. Los dos efectos de indexación hacen una pasada de
consistencia (ids, objetivos, dependencias) y reportan anomalías en vez de
fingir un reindexado.
"""
from __future__ import annotations

import logging
from typing import Any

from ._context import specifications, sprint_service, spring_id_of, task_id_of

logger = logging.getLogger(__name__)

_IN_FLOW = ("specified", "approved", "in_progress")


def _decision_of(params: dict[str, Any], kwargs: dict[str, Any]) -> str:
    """Decisión CAPTURADA por el gate.

    Ojo: `params["decision"]` es la condición declarada por el nodo
    (`{"decision": "approve"}`), no la decisión del usuario. Leerla como
    capturada hacía que el efecto se comparara consigo mismo y siempre pasara.
    """
    fields = kwargs.get("fields") or {}
    captured = ""
    if isinstance(fields, dict):
        captured = str(fields.get("decision") or "")
    if not captured:
        captured = str(params.get("captured_decision") or "")
    if captured:
        return captured
    state = kwargs.get("state") or kwargs.get("workflow_state")
    sub_data = getattr(state, "sub_state_data", None) if state is not None else None
    if isinstance(sub_data, dict):
        for key in reversed(list(sub_data.keys())):
            entry = sub_data[key]
            entry_fields = getattr(entry, "fields", None)
            if entry_fields is None and isinstance(entry, dict):
                entry_fields = entry.get("fields", entry)
            if isinstance(entry_fields, dict) and entry_fields.get("decision"):
                return str(entry_fields["decision"])
    return ""


def sprint_status(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Cambia el estado del sprint si el gate lo autorizó.

    `params.decision` es la condición (p. ej. `approve`): si la decisión
    registrada en el gate no coincide, no se toca el sprint. Así el efecto no
    adelanta estados que el usuario no aprobó.
    """
    params = params or {}
    target = str(params.get("status") or "").strip()
    required = str(params.get("decision") or "").strip()
    captured = _decision_of(params, kwargs)
    if required and captured and captured != required:
        return {
            "ok": True,
            "skipped": True,
            "reason": f"decisión {captured!r} != {required!r}",
        }
    if not target:
        return {"ok": False, "error": "sprint_status: falta 'status'"}

    service = sprint_service(chain_dir, params, kwargs)
    if service is None:
        return {
            "ok": False,
            "error": "sin sprint service (composición): el efecto no puede aplicarse",
        }
    spring_id = spring_id_of(service, chain_dir, params, kwargs)
    if not spring_id:
        return {"ok": True, "skipped": True, "reason": "sin sprint", "status": target}

    try:
        result = service.transition_spring(spring_id, target)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if isinstance(result, dict) and result.get("ok") is False:
        return {"ok": False, "error": result.get("error", "transición rechazada"), "status": target}
    return {"ok": True, "status": target, "spring_id": spring_id, "result": result}


def advance_sprint_task(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Avanza el cursor del sprint a la siguiente tarea.

    El cursor vive en `spring.context` (`current_task_index` + `task_id`), que
    es donde el DeltaGraph guarda el estado de negocio del sprint. Es
    idempotente y se clampa a la última spec: nunca crece fuera de rango.
    """
    params = params or {}
    service = sprint_service(chain_dir, params, kwargs)
    if service is None:
        return {
            "ok": False,
            "error": "sin sprint service (composición): el efecto no puede aplicarse",
        }
    spring_id = spring_id_of(service, chain_dir, params, kwargs)
    if not spring_id:
        return {"ok": True, "skipped": True, "reason": "sin sprint"}

    specs = specifications(service, spring_id)
    if not specs:
        return {"ok": True, "skipped": True, "reason": "sprint sin specs", "spring_id": spring_id}

    spring = service.get_spring(spring_id) or {}
    context = dict(spring.get("context") or {})
    # La tarea del workflow manda sobre el cursor guardado: es la que se acaba
    # de revisar. El cursor es el fallback para un efecto fuera de contexto.
    current = task_id_of(params, kwargs) or str(context.get("task_id") or "")
    ids = [str(s.get("spec_id") or "") for s in specs]
    index = ids.index(current) if current in ids else -1
    next_index = min(index + 1, len(specs) - 1)
    all_done = next_index >= len(specs) - 1 and next_index == index

    next_spec_id = ids[next_index] if ids else ""
    updates = {
        "current_task_index": next_index,
        "current_spec_index": next_index,
    }
    if next_spec_id:
        updates["task_id"] = next_spec_id
    try:
        service.update_spring(spring_id, {"context": {**context, **updates}})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "ok": True,
        "spring_id": spring_id,
        "current_task_index": next_index,
        "next_spec_id": next_spec_id,
        "previous_spec_id": current,
        "all_done": all_done,
    }


def archive_sprint(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Cierra el sprint al terminar. Idempotente.

    Delega en `SprintService.close_spring`: archivar tampoco puede dejar tareas
    abiertas en silencio. `force`/`reason` viajan en `params` para el cierre
    excepcional (queda registrado en la deuda del sprint).
    """
    params = params or {}
    service = sprint_service(chain_dir, params, kwargs)
    if service is None:
        return {
            "ok": False,
            "error": "sin sprint service (composición): el efecto no puede aplicarse",
        }
    spring_id = spring_id_of(service, chain_dir, params, kwargs)
    if not spring_id:
        return {"ok": True, "skipped": True, "reason": "sin sprint"}

    close = getattr(service, "close_spring", None)
    if not callable(close):
        return {
            "ok": False,
            "error": "archive_sprint: sprint service sin close_spring (composición rota)",
            "spring_id": spring_id,
        }
    try:
        result = close(
            spring_id,
            force=bool(params.get("force")),
            reason=str(params.get("reason") or ""),
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if isinstance(result, dict) and result.get("ok") is False:
        return {"ok": False, "error": result.get("error", "no se pudo cerrar"), "spring_id": spring_id}
    if isinstance(result, dict) and result.get("idempotent"):
        return {"ok": True, "archived": True, "spring_id": spring_id, "idempotent": True}
    return {"ok": True, "archived": True, "spring_id": spring_id, "result": result}


def _consistency(specs: list[dict]) -> tuple[int, list[dict]]:
    anomalies = [
        {
            "spec_id": str(s.get("spec_id") or ""),
            "missing": [
                field
                for field in ("spec_id", "objective")
                if not str(s.get(field) or "").strip()
            ],
        }
        for s in specs
    ]
    return len(specs), [a for a in anomalies if a["missing"]]


def index_sprint_tasks(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Verifica que las tareas del sprint sean consultables (delta = índice)."""
    params = params or {}
    service = sprint_service(chain_dir, params, kwargs)
    if service is None:
        return {
            "ok": False,
            "error": "sin sprint service (composición): el efecto no puede aplicarse",
        }
    spring_id = spring_id_of(service, chain_dir, params, kwargs)
    if not spring_id:
        return {"ok": True, "skipped": True, "reason": "sin sprint"}
    specs = specifications(service, spring_id)
    count, anomalies = _consistency(specs)
    return {
        "ok": True,
        "indexed": count,
        "anomalies": anomalies,
        "spring_id": spring_id,
        "source": "delta",
    }


def rebuild_sprint_index(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Pasada de consistencia de todas las specs al cerrar el sprint."""
    params = params or {}
    service = sprint_service(chain_dir, params, kwargs)
    if service is None:
        return {
            "ok": False,
            "error": "sin sprint service (composición): el efecto no puede aplicarse",
        }
    springs = []
    try:
        springs = [s for s in (service.list_springs() or []) if isinstance(s, dict)]
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    total = 0
    anomalies: list[dict] = []
    for spring in springs:
        spring_id = str(spring.get("spring_id") or "")
        specs = specifications(service, spring_id)
        count, found = _consistency(specs)
        total += count
        for anomaly in found:
            anomalies.append({"spring_id": spring_id, **anomaly})
    return {
        "ok": True,
        "indexed": total,
        "springs": len(springs),
        "anomalies": anomalies,
        "source": "delta",
    }


def create_adr(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Crea un ADR en el CKG si la spec se aprobó como decisión (idempotente)."""
    from ._context import ckg

    params = params or {}
    approval_type = str(params.get("approval_type") or "standard").strip().lower()
    if approval_type != "decision":
        return {
            "ok": True,
            "skipped": True,
            "reason": f"approval_type={approval_type!r} (no es decisión)",
        }

    service = sprint_service(chain_dir, params, kwargs)
    if service is None:
        return {
            "ok": False,
            "error": "sin sprint service (composición): el efecto no puede aplicarse",
        }
    spring_id = spring_id_of(service, chain_dir, params, kwargs)
    spec_id = task_id_of(params, kwargs)
    if not spring_id or not spec_id:
        return {"ok": True, "skipped": True, "reason": "sin sprint o spec"}

    spec = None
    try:
        spec = service.get_spec(spring_id, spec_id)
    except Exception:  # noqa: BLE001
        spec = None
    if not spec:
        return {"ok": True, "skipped": True, "reason": f"spec {spec_id} no encontrada"}
    related = spec.get("related_decisions") or []
    if isinstance(related, list) and related:
        return {
            "ok": True,
            "skipped": True,
            "reason": "la spec ya tiene ADR",
            "related_decisions": related,
        }

    graph = ckg(chain_dir)
    if graph is None:
        return {"ok": True, "skipped": True, "reason": "CKG no disponible"}

    existing = []
    try:
        existing = graph.query({"type": "decision", "metadata.spec_id": spec_id})
    except Exception:  # noqa: BLE001
        existing = []
    if existing:
        adr_id = str(existing[0].get("_id") or "")
        try:
            service.update_spec(
                spring_id,
                spec_id,
                {
                    "related_decisions": [adr_id],
                    "generated_from": f"SPEC:{spec_id}",
                },
            )
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "adr_id": adr_id, "spec_id": spec_id, "idempotent": True}

    objective = str(spec.get("objective") or spec.get("task_description") or spec_id)[:120]
    node = {
        "label": f"ADR: {objective}",
        "description": (
            f"Decisión arquitectónica generada desde {spec_id} "
            f"(aprobada como decisión)."
        ),
        "type": "decision",
        "metadata": {
            "spec_id": spec_id,
            "task_id": spec_id,
            "generated_from": f"SPEC:{spec_id}",
            "approval_type": "decision",
            "spring_id": spring_id,
        },
    }
    try:
        from fast_mcp.composition import ckg_save_nodes

        body = ckg_save_nodes(
            chain_dir,
            [node],
            task_id=spec_id,
            session_id=str(params.get("session_id") or ""),
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if not body.get("ok"):
        return {"ok": False, "error": body.get("error", "no se pudo guardar el ADR")}
    node_ids = list(body.get("node_ids") or [])
    if not node_ids:
        return {"ok": False, "error": "el CKG no devolvió node_id", "result": body}
    adr_id = str(node_ids[0])
    try:
        service.update_spec(
            spring_id,
            spec_id,
            {"related_decisions": [adr_id], "generated_from": f"SPEC:{spec_id}"},
        )
    except Exception as exc:  # noqa: BLE001 — el ADR ya existe; se reporta
        return {"ok": True, "adr_id": adr_id, "spec_id": spec_id, "link_error": str(exc)}
    return {"ok": True, "adr_id": adr_id, "spec_id": spec_id, "related_decisions": [adr_id]}


def register(registry) -> None:
    registry.register("sprint_status", sprint_status)
    registry.register("advance_sprint_task", advance_sprint_task)
    registry.register("archive_sprint", archive_sprint)
    registry.register("create_adr", create_adr)
    registry.register("index_sprint_tasks", index_sprint_tasks)
    registry.register("rebuild_sprint_index", rebuild_sprint_index)
