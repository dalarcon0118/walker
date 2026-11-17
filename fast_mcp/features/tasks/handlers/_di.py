"""_di.py — dependencias inyectadas a los handlers de tasks.

R1: `tasks` no puede importar `delta_tracking` (ni ninguna otra feature). El
composition root inyecta una factory `chain_dir -> SprintService` y los handlers
la consumen desde acá. Sin esto, `implement_task` no persiste la spec y
`verify_predicates` no encuentra los predicados de la tarea en curso.
"""
from __future__ import annotations

from typing import Any, Callable

_SPRINT_FACTORY: Callable[[str], Any] | None = None


def configure_sprint(factory: Callable[[str], Any] | None) -> None:
    global _SPRINT_FACTORY
    _SPRINT_FACTORY = factory


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
