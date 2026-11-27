"""handlers/spec_update.py — completar los campos de una spec por protocolo.

El nodo TASK_SPEC declara los campos obligatorios de una spec
(`result_state`, `problem_statement`, `predicates`, `files`, `preconditions`,
`acceptance_criteria`, `quality_gates`), pero fuera del side effect
`transform_spec` no había forma de escribirlos: `spec_predicates` sólo escribía
predicados y `spec_status` sólo cambiaba el estado. El agente quedaba con specs
en `draft` y sin manera de completarlas por las tools.

Este handler es el escritor explícito de esos campos. No cambia el estado
(eso es `spec_status`) ni los predicados (eso es `spec_predicates`): mezclar
las tres responsabilidades fue justamente lo que dejó huecos silenciosos.

Contrato:
  - `fields` es el dict de campos; también se aceptan como parámetros de primer
    nivel.
  - Sólo se escriben campos del modelo `Spec` de la whitelist.
  - `status` y `predicates` se rechazan con la tool que corresponde.
  - Devuelve `errors`/`ready` para que el caller sepa si ya puede pasar la spec
    a `specified`.
"""
from __future__ import annotations

import json
from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult
from fast_mcp.features.tasks.handlers import _di

_STRING_FIELDS = frozenset({
    "objective",
    "domain",
    "spec_type",
    "result_state",
    "description",
    "problem_statement",
    "loop_acceptance",
    "generated_from",
})

_LIST_FIELDS = frozenset({
    "files",
    "evidence",
    "acceptance_criteria",
    "quality_gates",
    "preconditions",
    "depends_on",
    "invariants",
    "loop_verification",
    "generates_specs",
    "related_decisions",
})

_DICT_FIELDS = frozenset({
    "impacted_artifacts",
    "task_context",
    "risks",
    "decision",
    "architecture",
    "implementation",
    "project_snapshot",
    "review_metrics",
})

_WRITABLE_FIELDS = _STRING_FIELDS | _LIST_FIELDS | _DICT_FIELDS

# Campos que tienen su propia tool (o que cierran la spec): no se pisan acá.
_RESERVED_FIELDS = {
    "status": "spec_status",
    "predicates": "spec_predicates",
    "completed_at": "spec_status",
    "commit_hash": "spec_status",
    "commit_url": "spec_status",
    "review_verdict": "spec_status",
    "reviewed_by": "spec_status",
    "review_findings": "spec_status",
}

_INCOMPLETE_STATUSES = frozenset({"draft", "rejected"})


def _as_list(value: Any) -> tuple[list | None, str]:
    if value is None:
        return [], ""
    if isinstance(value, str):
        text = value.strip()
        return ([text] if text else []), ""
    if isinstance(value, (list, tuple)):
        return [v for v in value if v not in (None, "")], ""
    return None, "se esperaba una lista o un string"


def _as_dict(value: Any) -> tuple[dict | None, str]:
    if value is None:
        return {}, ""
    if isinstance(value, dict):
        return dict(value), ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}, ""
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            return None, f"JSON inválido: {exc.msg}"
        if isinstance(parsed, dict):
            return parsed, ""
    return None, "se esperaba un objeto JSON"


def _normalize(fields: dict) -> tuple[dict, list[str]]:
    clean: dict[str, Any] = {}
    errors: list[str] = []
    for key, value in fields.items():
        if key in _STRING_FIELDS:
            clean[key] = "" if value is None else str(value)
        elif key in _LIST_FIELDS:
            parsed, error = _as_list(value)
            if error:
                errors.append(f"{key}: {error}")
            else:
                clean[key] = parsed
        elif key in _DICT_FIELDS:
            parsed, error = _as_dict(value)
            if error:
                errors.append(f"{key}: {error}")
            else:
                clean[key] = parsed
    return clean, errors


def spec_update(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    session_id = str(data.get("session_id") or ctx.session_id or "").strip()
    task_id = str(data.get("task_id") or ctx.task_id or "").strip()
    spec_id = str(data.get("spec_id") or "").strip()
    spring_id = str(data.get("spring_id") or data.get("sprint_id") or "").strip()

    raw = data.get("fields")
    if raw is not None and not isinstance(raw, dict):
        return TaskResult.failure("spec_update: `fields` debe ser un objeto")
    fields = dict(raw or {})
    for key in _WRITABLE_FIELDS:
        if key in data and data.get(key) is not None:
            fields[key] = data.get(key)
    if not fields:
        return TaskResult.failure(
            "spec_update: pasá `fields` con los campos de la spec; "
            f"escribibles: {', '.join(sorted(_WRITABLE_FIELDS))}"
        )

    reserved = sorted(set(fields) & set(_RESERVED_FIELDS))
    if reserved:
        remedies = ", ".join(f"{f}→{_RESERVED_FIELDS[f]}" for f in reserved)
        return TaskResult.failure(
            f"spec_update: {remedies}. Esos campos tienen su propia tool."
        )
    unknown = sorted(set(fields) - _WRITABLE_FIELDS)
    if unknown:
        return TaskResult.failure(
            "spec_update: campos desconocidos: "
            + ", ".join(unknown)
            + f". Escribibles: {', '.join(sorted(_WRITABLE_FIELDS))}"
        )

    service = getattr(ctx, 'sprint_service', None) or _di.sprint_service(ctx.chain_dir)
    if service is None:
        return TaskResult.failure("spec_update: sprint service no disponible")

    if not spring_id and session_id and hasattr(service, "get_session"):
        try:
            record = service.get_session(session_id) or {}
            spring_id = str(record.get("spring_id") or "")
        except Exception as exc:  # noqa: BLE001
            return TaskResult.failure(f"spec_update: {type(exc).__name__}: {exc}")
    if not spring_id:
        try:
            active = service.get_active_spring()
            spring_id = str((active or {}).get("spring_id") or "") if active else ""
        except Exception:  # noqa: BLE001 — sin activo se falla abajo
            spring_id = ""
    if not spring_id:
        return TaskResult.failure(
            "spec_update: no se pudo resolver el sprint (pasá spring_id o session_id)"
        )

    spec: dict | None = None
    if spec_id:
        spec = service.get_spec(spring_id, spec_id)
        if spec is None:
            return TaskResult.failure(
                f"spec_update: spec desconocida {spec_id!r} en {spring_id}"
            )
    else:
        spec = _di.current_spec(ctx.chain_dir, task_id=task_id, spring_id=spring_id)
        if spec is None:
            return TaskResult.failure(
                "spec_update: no hay spec para la tarea "
                f"{task_id or '(sin task_id)'} en {spring_id}; pasá spec_id"
            )
    real_spec_id = str(spec.get("spec_id") or "")

    clean, errors = _normalize(fields)
    if errors:
        return TaskResult.failure(
            "spec_update: campos inválidos — " + "; ".join(errors),
            data={"ok": False, "spec_id": real_spec_id, "errors": errors},
        )

    # `task_context` se MERGEA: pisarlo perdería task_id/session_id que el
    # workflow ya usa para atribuir la captura.
    if "task_context" in clean:
        clean["task_context"] = {
            **(spec.get("task_context") or {}),
            **(clean.get("task_context") or {}),
        }

    try:
        updated = service.update_spec(spring_id, real_spec_id, clean)
    except Exception as exc:  # noqa: BLE001
        return TaskResult.failure(f"spec_update: {type(exc).__name__}: {exc}")
    if updated is None:
        return TaskResult.failure(
            f"spec_update: no se pudo actualizar {real_spec_id} en {spring_id}"
        )

    spec_errors = _di.validate_spec(updated)
    status = str(updated.get("status") or "draft")
    ready = not spec_errors and status not in _INCOMPLETE_STATUSES
    next_step = (
        "spec_status status=specified"
        if not spec_errors and status in _INCOMPLETE_STATUSES
        else ("spec_start" if ready else "completar los campos que faltan")
    )
    return TaskResult.success(
        {
            "ok": True,
            "spring_id": spring_id,
            "spec_id": real_spec_id,
            "fields": sorted(clean),
            "spec": updated,
            "errors": spec_errors,
            "ready": ready,
            "next": next_step,
        }
    )


def register(registry) -> None:
    registry.register(
        "spec_update",
        spec_update,
        metadata={
            "description": "Complete the fields of an existing spec (not status/predicates)",
            "tags": ["session", "spec"],
            "scope": "internal",
        },
    )
