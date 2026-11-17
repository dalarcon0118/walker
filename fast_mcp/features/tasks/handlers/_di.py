"""_di.py — dependencias inyectadas a los handlers de tasks.

R1: `tasks` no puede importar `delta_tracking` (ni ninguna otra feature). El
composition root inyecta una factory `chain_dir -> SprintService` y los handlers
la consumen desde acá. Sin esto, `implement_task` no persiste la spec y
`verify_predicates` no encuentra los predicados de la tarea en curso.
"""
from __future__ import annotations

from typing import Any, Callable

_SPRINT_FACTORY: Callable[[str], Any] | None = None
_TASK_RECORDER: Callable[[str, str], bool] | None = None
_KNOWLEDGE_WRITER: Callable[..., Any] | None = None
_TRACE_READER: Callable[[str, str], list] | None = None


def configure_sprint(factory: Callable[[str], Any] | None) -> None:
    global _SPRINT_FACTORY
    _SPRINT_FACTORY = factory


def configure_task_recorder(recorder: Callable[[str, str], bool] | None) -> None:
    """Inyecta `(chain_dir, task_id) -> bool`: guarda la tarea en el workflow."""
    global _TASK_RECORDER
    _TASK_RECORDER = recorder


def configure_knowledge_writer(writer: Callable[..., Any] | None) -> None:
    """Inyecta `(chain_dir, nodes, task_id, session_id) -> resultado` para el CKG."""
    global _KNOWLEDGE_WRITER
    _KNOWLEDGE_WRITER = writer


def configure_trace_reader(reader: Callable[[str, str], list] | None) -> None:
    """Inyecta `(chain_dir, session_id) -> [eventos]` (R1: tasks no lee tracing)."""
    global _TRACE_READER
    _TRACE_READER = reader


def session_events(chain_dir: str, session_id: str) -> list:
    if _TRACE_READER is None or not chain_dir or not session_id:
        return []
    try:
        return list(_TRACE_READER(chain_dir, session_id) or [])
    except Exception:  # noqa: BLE001
        return []


def save_knowledge(chain_dir: str, nodes: list[dict], task_id: str = "", session_id: str = ""):
    if _KNOWLEDGE_WRITER is None or not chain_dir or not nodes:
        return None
    try:
        return _KNOWLEDGE_WRITER(chain_dir, nodes, task_id, session_id)
    except Exception:  # noqa: BLE001 — documentar no puede romper el workflow
        return None


def record_task(chain_dir: str, task_id: str) -> bool:
    if _TASK_RECORDER is None or not chain_dir or not task_id:
        return False
    try:
        return bool(_TASK_RECORDER(chain_dir, task_id))
    except Exception:  # noqa: BLE001 — recordar la tarea no puede romper la tool
        return False


def sprint_service(chain_dir: str):
    """SprintService del proyecto, o None si no se inyectó / no se puede armar."""
    if _SPRINT_FACTORY is None or not chain_dir:
        return None
    return _SPRINT_FACTORY(chain_dir)


def current_spec(
    chain_dir: str,
    task_id: str = "",
    prefer_status: tuple[str, ...] = ("in_progress",),
) -> dict | None:
    """Spec de la tarea en curso.

    Con `task_id` se busca exacta (por `task_context.task_id` o `spec_id`).
    Sin id —el motor no siempre lo conoce— se cae a la más reciente en
    `in_progress`, que es una heurística y así se documenta.
    """
    service = sprint_service(chain_dir)
    if service is None:
        return None
    springs = []
    active = service.get_active_spring()
    if active:
        springs.append(active)
    springs.extend(service.list_springs() or [])

    seen: set[str] = set()
    candidates: list[dict] = []
    for spring in springs:
        sid = str(spring.get("spring_id") or spring.get("id") or "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        candidates.extend(service.get_specs(sid) or [])

    # Id exacto primero: `implement_task` guarda el id real en task_context.
    if task_id:
        for spec in candidates:
            if str((spec.get("task_context") or {}).get("task_id", "")) == task_id:
                return spec
            if str(spec.get("spec_id", "")) == task_id:
                return spec

    for status in prefer_status:
        matching = [s for s in candidates if str(s.get("status", "")) == status]
        if matching:
            matching.sort(key=lambda s: str(s.get("updated_at") or s.get("created_at") or ""))
            return matching[-1]
    return None
