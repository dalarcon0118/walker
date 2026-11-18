"""domain/spec_service.py — Domain services for specs and sprints.

SSOT for validation, construction, and status transitions.
Pure domain logic — no infrastructure dependencies.

Result pattern (dry-python/returns): pure functions use @safe
to avoid try/catch and propagate failures without exceptions.
"""
from __future__ import annotations

import json
from typing import Any

from returns.result import Result, Success, Failure

from fast_mcp.features.delta_tracking.domain.entities import Spec, _now
from fast_mcp.features.delta_tracking.domain.values import (
    SPRING_STATUS_FLOW,
    SPRING_TRANSITIONS,
)


# ── SSOT: specification field definitions ──────────────────────────────────

# Campos SIN los cuales una spec no es verificable. Alineados al modelo real
# (`Spec` tiene 39 campos y NO tiene `task_description`: la lista anterior pedía
# un campo inexistente y `validate_spec` leía `validation.predicates` /
# `impacted_artifacts.files`, rutas que nada escribe). Como nadie llamaba al
# validador, la desalineación no se notó.
SPEC_REQUIRED_FIELDS = [
    "objective",
    "files",
]

# Lo que además hace falta para poder VERIFICAR (una de las dos).
SPEC_VERIFICATION_FIELDS = ["acceptance_criteria", "predicates"]

SPEC_STATUS_FLOW = ["draft", "specified", "approved", "in_progress", "in_review", "completed"]


def _completion_missing(spec: dict) -> list[str]:
    """Campos que faltan para poder cerrar una spec como `completed`.

    REPORT_GATE aporta commit_hash/commit_url/completed_at;
    VERIFY/QA aporta review_verdict y evidence.
    """
    missing: list[str] = []
    if not spec.get("completed_at"):
        missing.append("completed_at (REPORT_GATE)")
    if not spec.get("commit_hash"):
        missing.append("commit_hash (REPORT_GATE)")
    if not spec.get("review_verdict"):
        missing.append("review_verdict (VERIFY/QA)")
    ev = spec.get("evidence")
    if not ev or (isinstance(ev, (list, tuple, dict)) and len(ev) == 0):
        missing.append("evidence (VERIFY/QA, at least one item)")
    return missing


# ── SpecValidator ─────────────────────────────────────────────────────────

class SpecValidator:
    """Valida una spec contra el modelo REAL (`Spec`), no contra un esquema viejo."""

    @staticmethod
    def validate_fields(data: dict) -> list[str]:
        """Chequeo mínimo sobre un payload de creación."""
        errors: list[str] = []
        for field in SPEC_REQUIRED_FIELDS:
            val = data.get(field)
            if not val or (isinstance(val, (list, tuple, dict)) and len(val) == 0):
                errors.append(f"{field}: is required and cannot be empty")
        if not SpecValidator._has_verification(data):
            errors.append(
                "verification: hace falta acceptance_criteria o predicates con items"
            )
        return errors

    @staticmethod
    def _predicate_items(spec: dict) -> list:
        preds = spec.get("predicates")
        if isinstance(preds, dict):
            return [p for p in (preds.get("items") or []) if p]
        if isinstance(preds, list):
            return [p for p in preds if p]
        return []

    @staticmethod
    def _has_verification(spec: dict) -> bool:
        return bool(spec.get("acceptance_criteria")) or bool(SpecValidator._predicate_items(spec))

    @staticmethod
    def validate_spec(spec: dict, require_status: str = "") -> list[str]:
        """Errores que impiden considerar la spec completa.

        `require_status` es opcional: el escritor pone `in_progress`, así que
        exigir `specified` a secas rechazaba specs válidas.
        """
        sid = spec.get("spec_id", spec.get("task_id", "?"))
        errors: list[str] = []
        for field in SPEC_REQUIRED_FIELDS:
            val = spec.get(field)
            if not val or (isinstance(val, (list, tuple, dict)) and len(val) == 0):
                errors.append(f"{sid}: {field} is empty")
        if not SpecValidator._has_verification(spec):
            errors.append(f"{sid}: sin criterios de aceptación ni predicados ejecutables")
        if require_status and spec.get("status", "") != require_status:
            errors.append(f"{sid}: status is '{spec.get('status', '')}' must be '{require_status}'")
        # Invariante de cierre: una spec `completed` exige artefactos.
        if spec.get("status") == "completed" or require_status == "completed":
            for missing in _completion_missing(spec):
                errors.append(f"{sid}: {missing}")
        return errors

    @staticmethod
    def completeness(spec: dict, model_fields: list[str] | None = None) -> dict:
        """Cuántos campos del modelo están llenos, y cuáles faltan."""
        from dataclasses import fields as _fields

        names = model_fields or [f.name for f in _fields(Spec)]
        missing = [
            name for name in names
            if spec.get(name) in (None, "", [], {})
        ]
        return {
            "filled": len(names) - len(missing),
            "total": len(names),
            "missing": missing,
        }


class SpecStatusUpdater:
    @staticmethod
    def update(spec: dict, new_status: str) -> dict:
        if new_status not in SPEC_STATUS_FLOW:
            raise ValueError(f"Invalid status '{new_status}'. Valid: {SPEC_STATUS_FLOW}")
        current = spec.get("status", "draft")
        if current not in SPEC_STATUS_FLOW:
            current = "draft"
        current_idx = SPEC_STATUS_FLOW.index(current)
        new_idx = SPEC_STATUS_FLOW.index(new_status)
        if new_idx < current_idx:
            raise ValueError(f"Cannot move status back from '{current}' to '{new_status}'")
        # Invariante de cierre: `completed` exige artefactos.
        if new_status == "completed":
            missing = _completion_missing(spec)
            if missing:
                raise ValueError(
                    "Cannot mark spec as completed: missing required artifacts: "
                    + ", ".join(missing)
                )
        spec = dict(spec)
        spec["status"] = new_status
        return spec

    @staticmethod
    def reopen(spec: dict, reason: str = "") -> dict:
        """Vuelve a `specified` de forma explícita, registrando el motivo.

        El flujo normal prohíbe retroceder; esta es la vía explícita.
        """
        from datetime import datetime, timezone

        data = dict(spec or {})
        data["status"] = "specified"
        now = datetime.now(timezone.utc).isoformat()
        tc = dict(data.get("task_context") or {})
        tc["reopen_reason"] = reason
        tc["reopened_at"] = now
        data["task_context"] = tc
        data["reopen_reason"] = reason
        data["reopened_at"] = now
        return data

    # Alias para compatibilidad con nombres esperados por callers.
    reopen_spec = reopen


# ── SprintStatusUpdater ──────────────────────────────────────────────────

class SprintStatusUpdater:
    @staticmethod
    def can_transition(current: str, new_status: str) -> tuple[bool, str]:
        # SSOT en values.py. Antes había una copia local con typo
        # (`SPRINT_STATUS_FLOW`) que ignoraba los cambios del dominio.
        if new_status not in SPRING_STATUS_FLOW:
            return False, f"Invalid sprint status '{new_status}'. Valid: {SPRING_STATUS_FLOW}"
        if current not in SPRING_STATUS_FLOW:
            current = "planning"
        # IDEMPOTENCY: same status = successful no-op
        if new_status == current:
            return True, ""
        allowed = SPRING_TRANSITIONS.get(current, frozenset())
        if new_status in allowed:
            return True, ""
        return False, (
            f"Cannot move sprint status from '{current}' to '{new_status}'. "
            f"Allowed from '{current}': {sorted(allowed) or ['(terminal)']}"
        )


# ── SpecService (domain) ─────────────────────────────────────────────────

class SpecService:
    """Pure domain operations on Spec."""

    @staticmethod
    def merge(spec: Spec, updates: dict) -> Spec:
        """Merge updates from a dict (partial update)."""
        data = spec.to_dict()
        data.update({k: v for k, v in updates.items() if v is not None})
        if "status" in updates and updates["status"] is not None:
            data["status"] = updates["status"]
        # Invariante de cierre: `completed` exige artefactos.
        if data.get("status") == "completed":
            missing = _completion_missing(data)
            if missing:
                raise ValueError(
                    "Cannot mark spec as completed: missing required artifacts: "
                    + ", ".join(missing)
                )
        merged = Spec.from_dict(data)
        # Preservar metadata de reopen si venía en updates.
        if isinstance(updates, dict) and "reopen_reason" in updates:
            try:
                merged.reopen_reason = updates.get("reopen_reason")
            except Exception:
                pass
        return merged

    @staticmethod
    def reopen(spec, reason: str = "") -> Spec:
        """Vuelve a `specified` de forma explícita, registrando el motivo."""
        from datetime import datetime, timezone

        data = spec.to_dict() if hasattr(spec, "to_dict") else dict(spec or {})
        data["status"] = "specified"
        now = datetime.now(timezone.utc).isoformat()
        tc = dict(data.get("task_context") or {})
        tc["reopen_reason"] = reason
        tc["reopened_at"] = now
        data["task_context"] = tc
        merged = Spec.from_dict(data)
        try:
            merged.reopen_reason = reason
            merged.reopened_at = now
        except Exception:
            pass
        return merged

    # Alias para compatibilidad con nombres esperados por callers.
    reopen_spec = reopen

    @staticmethod
    def refine(existing: Spec, data: dict) -> Spec:
        """Refine an existing spec with new data (legacy-compatible)."""
        tc_raw = data.get("task_context", "{}")
        tc_result = _safe_json_loads(tc_raw) if isinstance(tc_raw, str) else Success(tc_raw)
        task_context = tc_result.value_or(existing.to_dict().get("task_context", {}))

        merged = {
            "spec_id": existing.spec_id,
            "spring_id": existing.spring_id,
            "objective": data.get("objective", data.get("title", existing.objective)),
            "domain": data.get("domain", existing.domain),
            "result_state": data.get("result_state", existing.result_state),
            "task_description": data.get("task_description", data.get("description", existing.description)),
            "validation": {
                "predicates": _parse_field(data, "predicates", existing.to_dict().get("validation", {}).get("predicates", [])),
            },
            "impacted_artifacts": {
                "files": _parse_field(data, "files", existing.to_dict().get("impacted_artifacts", {}).get("files", [])),
            },
            "depends_on": _parse_field(data, "depends_on", existing.depends_on),
            "risks": {
                "curvature": float(data.get("curvature", existing.to_dict().get("risks", {}).get("curvature", 0.3))),
                "pre_churn": float(data.get("pre_churn", existing.to_dict().get("risks", {}).get("pre_churn", 0.3))),
                "commit_trust": float(data.get("commit_trust", existing.to_dict().get("risks", {}).get("commit_trust", 0.8))),
            },
            "preconditions": _parse_field(data, "preconditions", existing.to_dict().get("preconditions", [])),
            "acceptance_criteria": _parse_field(data, "acceptance_criteria", existing.to_dict().get("acceptance_criteria", [])),
            "quality_gates": _parse_field(data, "quality_gates", existing.to_dict().get("quality_gates", [])),
            "task_context": task_context,
            "loop_verification": _parse_field(data, "loop_verification", existing.to_dict().get("loop_verification", [])),
            "loop_acceptance": data.get("loop_acceptance", existing.to_dict().get("loop_acceptance", "all")),
            "status": "specified" if data.get("result_state") or data.get("task_description") else existing.status,
        }
        return Spec.from_dict(merged)


# ── Helpers ────────────────────────────────────────────────────────────────

def _safe_json_loads(value: str) -> Result[Any, str]:
    """Parse JSON safely using Result pattern."""
    try:
        return Success(json.loads(value))
    except (json.JSONDecodeError, TypeError) as e:
        return Failure(f"json_parse_failed: {type(e).__name__}: {e}")


def _parse_field(data: dict, field: str, default=None):
    val = data.get(field, default)
    if isinstance(val, str):
        val = val.strip()
        if val.startswith(("[", "{")):
            result = _safe_json_loads(val)
            if isinstance(result, Failure):
                return default
            return result.unwrap()
    return val
