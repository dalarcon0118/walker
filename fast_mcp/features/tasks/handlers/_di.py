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
_CONTRACT_VALIDATOR: Callable[[dict], list] | None = None


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


def configure_contract_validator(validator: Callable[[dict], list] | None) -> None:
    """Inyecta `contract -> [errores]` (R1: tasks no importa delta_tracking).

    El schema/validador del WorkContract vive en `delta_tracking/domain`
    (F0a). El composition root lo cablea acá para que capture/spec_status lo
    usen sin violar la regla de dependencias (ADR-WALKER-086).
    """
    global _CONTRACT_VALIDATOR
    _CONTRACT_VALIDATOR = validator


def validate_contract(contract: dict) -> list[str]:
    """Valida un WorkContract con el validador inyectado (vacío = válido).

    Fail-fast: si no hay validador inyectado se reporta como error; un contrato
    que no se puede validar NO debe persistirse.
    """
    if _CONTRACT_VALIDATOR is None:
        return ["validador de contrato no configurado (composición rota)"]
    try:
        return list(_CONTRACT_VALIDATOR(contract) or [])
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


def session_spring(chain_dir: str, session_id: str) -> str:
    """Sprint al que pertenece la sesión ('' si no está registrada).

    La captura lo usa para acotar la resolución de una spec: los ids `SPEC-NNN`
    se repiten en cada sprint y resolverlos sin alcance elige el primero del
    índice (bug medido 2026-11-20: SPEC-012 de otro sprint).
    """
    try:
        service = sprint_service(chain_dir)
    except Exception:  # noqa: BLE001 — un chain_dir no resoluble no da alcance
        return ""
    if service is None or not session_id:
        return ""
    try:
        record = service.get_session(session_id) or {}
    except Exception:  # noqa: BLE001 — sin sesión no hay alcance, no es fatal
        return ""
    return str(record.get("spring_id") or "")


def current_spec(
    chain_dir: str,
    task_id: str = "",
    prefer_status: tuple[str, ...] = ("in_progress",),
    exact: bool = False,
    spring_id: str = "",
) -> dict | None:
    """Spec de la tarea en curso.

    Con `task_id` se busca exacta (por `task_context.task_id` o `spec_id`).
    Sin id —el motor no siempre lo conoce— se cae a la más reciente en
    `in_progress`, que es una heurística y así se documenta.

    `exact=True` desactiva la heurística: solo devuelve la spec cuyo id
    coincide. La usa la captura para no heredar la identidad de otra spec
    `in_progress` cuando el caller nombra una tarea que no está en el sprint.

    `spring_id` acota la búsqueda a ese sprint. Sin él, si el id aparece en más
    de un sprint, devuelve `None` en vez de elegir el primero: un id de spec es
    local a su sprint y elegir arbitrariamente firma el hecho con otra tarea.
    """
    service = sprint_service(chain_dir)
    if service is None:
        return None
    springs = []
    if spring_id:
        springs.append({"spring_id": spring_id})
    else:
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
        # Priorizar coincidencia exacta por spec_id (más específico que
        # task_context.task_id, que puede estar duplicado por implement_task).
        spec_id_matches = [
            spec for spec in candidates
            if str(spec.get("spec_id", "")) == task_id
        ]
        # Si hay spring_id, acotar a ese sprint (evita ambigüedad entre sprints).
        # Solo filtrar si la spec declara spring_id; las specs legacy (tests/fakes)
        # no lo llevan y no deben descartarse.
        if spring_id:
            scoped = [s for s in spec_id_matches if str(s.get("spring_id", "")) == spring_id]
            if scoped:
                spec_id_matches = scoped
        if len(spec_id_matches) == 1:
            return spec_id_matches[0]
        if len(spec_id_matches) > 1 and not spring_id:
            return None  # ambiguo entre sprints: el caller debe pasar spring_id
        if spec_id_matches:
            return spec_id_matches[0]
        tc_matches = [
            spec for spec in candidates
            if str((spec.get("task_context") or {}).get("task_id", "")) == task_id
        ]
        if spring_id:
            scoped_tc = [s for s in tc_matches if str(s.get("spring_id", "")) == spring_id]
            if scoped_tc:
                tc_matches = scoped_tc
        if len(tc_matches) == 1:
            return tc_matches[0]
        if tc_matches and not exact:
            return tc_matches[0]
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
