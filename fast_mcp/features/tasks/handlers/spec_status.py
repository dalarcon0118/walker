"""handlers/spec_status.py — transición explícita de una spec (tarea).

Cierra el hueco del cierre de sprint: una spec solo puede pasar a `completed`
con sus artefactos (completed_at, commit_hash, review_verdict y evidence), y
hasta ahora no había forma pública de escribirlos. `close_sprint` rechaza el
cierre con tareas abiertas y señala este tool como la salida accionable.

La invariante NO vive acá: `SprintService.update_spec_status` aplica los
artefactos y el status en el MISMO save, y `SpecStatusUpdater` rechaza el
`completed` sin artefactos.
"""
from __future__ import annotations

from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult
from fast_mcp.features.tasks.handlers import _di

_ARTIFACT_FIELDS = ("completed_at", "commit_hash", "commit_url", "review_verdict", "evidence")


def spec_status(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    spec_id = str(data.get("spec_id") or data.get("task_id") or "").strip()
    status = str(data.get("status") or "").strip()
    session_id = str(data.get("session_id") or ctx.session_id or "").strip()
    spring_id = str(data.get("spring_id") or data.get("sprint_id") or "").strip()

    if not spec_id:
        return TaskResult.failure("spec_status: spec_id requerido (ej. SPEC-002)")
    if not status:
        return TaskResult.failure("spec_status: status requerido")

    service = _di.sprint_service(ctx.chain_dir)
    if service is None:
        return TaskResult.failure("spec_status: sprint service no disponible")

    if not spring_id and session_id and hasattr(service, "get_session"):
        try:
            record = service.get_session(session_id) or {}
            spring_id = str(record.get("spring_id") or "")
        except Exception as exc:  # noqa: BLE001
            return TaskResult.failure(f"spec_status: {type(exc).__name__}: {exc}")
    if not spring_id:
        try:
            active = service.get_active_spring()
            spring_id = (active.get("spring_id") or active.get("_id") or "") if active else ""
        except Exception:  # noqa: BLE001 — sin activo se falla abajo
            spring_id = ""
    if not spring_id:
        return TaskResult.failure(
            "spec_status: no se pudo resolver el sprint (pasá spring_id o session_id)"
        )

    spec = service.get_spec(spring_id, spec_id)
    if spec is None:
        return TaskResult.failure(f"spec_status: spec desconocida {spec_id!r} en {spring_id}")

    artifacts = {
        key: data.get(key)
        for key in _ARTIFACT_FIELDS
        if data.get(key) not in (None, "", [])
    }
    try:
        updated = service.update_spec_status(spring_id, spec_id, status, artifacts=artifacts)
    except ValueError as exc:
        return TaskResult.failure(f"spec_status: {exc}")
    except Exception as exc:  # noqa: BLE001
        return TaskResult.failure(f"spec_status: {type(exc).__name__}: {exc}")
    if updated is None:
        return TaskResult.failure(f"spec_status: no se pudo actualizar {spec_id}")

    return TaskResult.success(
        {
            "ok": True,
            "spring_id": spring_id,
            "spec_id": spec_id,
            "task_id": str((updated.get("task_context") or {}).get("task_id") or spec_id),
            "status": updated.get("status") or status,
            "spec": updated,
        }
    )


def register(registry) -> None:
    registry.register(
        "spec_status",
        spec_status,
        metadata={
            "description": "Transition a spec (task) with closing artifacts",
            "tags": ["session", "spec", "close"],
            "scope": "internal",
        },
    )
