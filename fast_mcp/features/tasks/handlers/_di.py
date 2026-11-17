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


def current_spec(chain_dir: str, prefer_status: tuple[str, ...] = ("in_progress",)) -> dict | None:
    """Spec de la tarea en curso: la más reciente con el status pedido.

    No hay `task_id` confiable en el contexto del motor (el orquestador lo
    rellena con el NOMBRE DE LA ACCIÓN), así que se toma la spec más reciente en
    `in_progress` del sprint activo o, si no hay, de cualquiera.
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

    for status in prefer_status:
        matching = [s for s in candidates if str(s.get("status", "")) == status]
        if matching:
            matching.sort(key=lambda s: str(s.get("updated_at") or s.get("created_at") or ""))
            return matching[-1]
    return None
