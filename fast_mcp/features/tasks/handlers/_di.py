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
_WORKFLOW_RESET: Callable[[str], dict] | None = None
_KNOWLEDGE_WRITER: Callable[..., Any] | None = None
_KNOWLEDGE_READER: Callable[[str], list] | None = None
_TRACE_WRITER: Callable[[str, dict], bool] | None = None
_TRACE_READER: Callable[[str, str], list] | None = None
_SPEC_VALIDATOR: Callable[[dict], list] | None = None


def configure_sprint(factory: Callable[[str], Any] | None) -> None:
    global _SPRINT_FACTORY
    _SPRINT_FACTORY = factory


def configure_task_recorder(recorder: Callable[[str, str], bool] | None) -> None:
    """Inyecta `(chain_dir, task_id) -> bool`: guarda la tarea en el workflow."""
    global _TASK_RECORDER
    _TASK_RECORDER = recorder


def configure_workflow_reset(reset: Callable[[str], dict] | None) -> None:
    """Inyecta `(chain_dir) -> {ok, changed}`: reposiciona el workflow.

    Lo usa `session_bind mode=new` cuando el sprint anterior está cerrado: si
    el workflow se queda en SPRINT.CLOSE, el auto-advance cierra el sprint
    recién creado en el siguiente turno.
    """
    global _WORKFLOW_RESET
    _WORKFLOW_RESET = reset


def reset_workflow_active(chain_dir: str) -> dict:
    """Reposiciona el workflow en SPRINT.ACTIVE si está en CLOSE (fail-loud)."""
    if _WORKFLOW_RESET is None:
        return {
            "ok": False,
            "error": "reset de workflow no configurado (composición rota)",
        }
    try:
        return dict(_WORKFLOW_RESET(chain_dir) or {})
    except Exception as exc:  # noqa: BLE001 — se reporta al caller
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def configure_knowledge_writer(writer: Callable[..., Any] | None) -> None:
    """Inyecta `(chain_dir, nodes, task_id, session_id) -> resultado` para el CKG."""
    global _KNOWLEDGE_WRITER
    _KNOWLEDGE_WRITER = writer


def configure_knowledge_reader(reader: Callable[[str], list] | None) -> None:
    """Inyecta `chain_dir -> [nodos session activos]` (retención, R1).

    Lo usa `capture_knowledge` para superseder: la política de qué nodo viejo
    reemplaza al nuevo se decide con LECTURA del CKG, y tasks no puede importar
    cognitive_knowledge.
    """
    global _KNOWLEDGE_READER
    _KNOWLEDGE_READER = reader


def configure_trace_writer(writer: Callable[[str, dict], bool] | None) -> None:
    """Inyecta `(chain_dir, evento) -> ok` para registrar verificación (P1.11).

    `tasks` no puede importar `event_tracing` (R1): el puente lo cablea el
    composition root, igual que knowledge/trace readers.
    """
    global _TRACE_WRITER
    _TRACE_WRITER = writer


def push_trace(chain_dir: str, event: dict) -> bool:
    """Escribe un evento de trace; False si no hay writer (no rompe la tool)."""
    if _TRACE_WRITER is None or not chain_dir or not event:
        return False
    try:
        return bool(_TRACE_WRITER(chain_dir, event))
    except Exception:  # noqa: BLE001 — la verificación no depende del trace
        return False


def session_facts(chain_dir: str) -> list:
    """Nodos `session` activos del CKG (vacío si no hay lector inyectado)."""
    if _KNOWLEDGE_READER is None or not chain_dir:
        return []
    try:
        return list(_KNOWLEDGE_READER(chain_dir) or [])
    except Exception:  # noqa: BLE001 — la retención no puede romper la captura
        return []


def configure_spec_validator(validator: Callable[[dict], list] | None) -> None:
    """Inyecta `spec -> [errores]` (R1: tasks no importa delta_tracking)."""
    global _SPEC_VALIDATOR
    _SPEC_VALIDATOR = validator


def validate_spec(spec: dict) -> list[str]:
    if _SPEC_VALIDATOR is None:
        return []
    try:
        return list(_SPEC_VALIDATOR(spec) or [])
    except Exception as exc:  # noqa: BLE001
        return [f"validador falló: {type(exc).__name__}: {exc}"]


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
    exact: bool = False,
) -> dict | None:
    """Spec de la tarea en curso.

    Con `task_id` se busca exacta (por `task_context.task_id` o `spec_id`).
    Sin id —el motor no siempre lo conoce— se cae a la más reciente en
    `in_progress`, que es una heurística y así se documenta.

    `exact=True` desactiva la heurística: solo devuelve la spec cuyo id
    coincide. La usa la captura para no heredar la identidad de otra spec
    `in_progress` cuando el caller nombra una tarea que no está en el sprint.
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
        if exact:
            return None

    if exact:
        return None

    for status in prefer_status:
        matching = [s for s in candidates if str(s.get("status", "")) == status]
        if matching:
            matching.sort(key=lambda s: str(s.get("updated_at") or s.get("created_at") or ""))
            return matching[-1]
    return None
