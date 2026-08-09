"""domain/services/spec_service.py — Servicios de dominio para specs y sprints.

SSOT de validacion, construccion y transiciones de estado para specs y sprints.
Movido desde features/spring/domain/services.py para concentrar en
delta_tracking todo lo relacionado con el spring (SRP).
"""
from __future__ import annotations

import json
from typing import Any

from features.delta_tracking.domain.entities import Spec


# ── SSOT: specification field definitions ──────────────────────────────────

SPEC_REQUIRED_FIELDS = [
    "result_state", "task_description", "predicates", "files",
    "preconditions", "acceptance_criteria", "quality_gates",
]

SPEC_DEFAULTS: dict[str, Any] = {
    "type": "feature", "domain": "",
    "result_state": "", "task_description": "",
    "validation": {"predicates": []},
    "impacted_artifacts": {"files": []},
    "depends_on": [],
    "risks": {"curvature": 0.3, "pre_churn": 0.3, "commit_trust": 0.8},
    "preconditions": [], "acceptance_criteria": [], "quality_gates": [],
    "task_context": {}, "decisions": [],
    "loop_verification": [], "loop_acceptance": "all",
    "status": "draft",
}

SPEC_STATUS_FLOW = ["draft", "specified", "in_progress", "completed"]
SPRINT_STATUS_FLOW = ["planning", "active", "reviewing", "closed"]


# ── SpecValidator ─────────────────────────────────────────────────────────

class SpecValidator:
    @staticmethod
    def validate_fields(data: dict) -> list[str]:
        errors: list[str] = []
        for field in SPEC_REQUIRED_FIELDS:
            val = _parse_field(data, field)
            if not val:
                errors.append(f"{field}: is required and cannot be empty")
            elif isinstance(val, (list, tuple)) and len(val) == 0:
                errors.append(f"{field}: must have at least one element")
        return errors

    @staticmethod
    def validate_spec(spec: dict) -> list[str]:
        errors: list[str] = []
        sid = spec.get("spec_id", spec.get("task_id", "?"))
        for field in SPEC_REQUIRED_FIELDS:
            if field == "predicates":
                val = spec.get("validation", {}).get("predicates", [])
            elif field == "files":
                val = spec.get("impacted_artifacts", {}).get("files", [])
            else:
                val = spec.get(field, "")
            if not val:
                errors.append(f"{sid}: {field} is empty")
            elif isinstance(val, (list, tuple)) and len(val) == 0:
                errors.append(f"{sid}: {field} must have at least one element")
        if spec.get("status", "") != "specified":
            errors.append(f"{sid}: status is '{spec.get('status', '')}' must be 'specified'")
        if not spec.get("objective"):
            errors.append(f"{sid}: objective (title) is empty")
        return errors


# ── SpecBuilder ───────────────────────────────────────────────────────────

class SpecBuilder:
    @staticmethod
    def create_default(index: int, data: dict) -> dict:
        return {
            "spec_id": data.get("spec_id", data.get("task_id", f"SPEC-{index + 1:03d}")),
            "objective": data.get("title", data.get("objective", "")),
            **{k: v for k, v in SPEC_DEFAULTS.items() if k != "type"},
            "type": "feature",
        }

    @staticmethod
    def refine(existing: dict, data: dict) -> dict:
        tc_raw = data.get("task_context", "{}")
        try:
            task_context = json.loads(tc_raw) if isinstance(tc_raw, str) else tc_raw
        except (json.JSONDecodeError, TypeError):
            task_context = existing.get("task_context", {})

        return {
            "spec_id": existing.get("spec_id", existing.get("task_id", data.get("spec_id", data.get("task_id", "")))),
            "objective": data.get("objective", data.get("title", existing.get("objective", ""))),
            "type": data.get("type", existing.get("type", "feature")),
            "domain": data.get("domain", existing.get("domain", "")),
            "result_state": data.get("result_state", existing.get("result_state", "")),
            "task_description": data.get("task_description", data.get("description", existing.get("task_description", ""))),
            "validation": {
                "predicates": _parse_field(data, "predicates",
                    existing.get("validation", {}).get("predicates", [])),
            },
            "impacted_artifacts": {
                "files": _parse_field(data, "files",
                    existing.get("impacted_artifacts", {}).get("files", [])),
            },
            "depends_on": _parse_field(data, "depends_on", existing.get("depends_on", [])),
            "risks": {
                "curvature": float(data.get("curvature", existing.get("risks", {}).get("curvature", 0.3))),
                "pre_churn": float(data.get("pre_churn", existing.get("risks", {}).get("pre_churn", 0.3))),
                "commit_trust": float(data.get("commit_trust", existing.get("risks", {}).get("commit_trust", 0.8))),
            },
            "preconditions": _parse_field(data, "preconditions", existing.get("preconditions", [])),
            "acceptance_criteria": _parse_field(data, "acceptance_criteria", existing.get("acceptance_criteria", [])),
            "quality_gates": _parse_field(data, "quality_gates", existing.get("quality_gates", [])),
            "task_context": task_context,
            "decisions": _parse_field(data, "decisions", existing.get("decisions", [])),
            "loop_verification": _parse_field(data, "loop_verification", existing.get("loop_verification", [])),
            "loop_acceptance": data.get("loop_acceptance", existing.get("loop_acceptance", "all")),
            "status": "specified" if data.get("result_state") or data.get("task_description") else "draft",
        }


# ── SpecStatusUpdater ────────────────────────────────────────────────────

class SpecStatusUpdater:
    @staticmethod
    def update(spec: dict, new_status: str) -> dict:
        if new_status not in SPEC_STATUS_FLOW:
            raise ValueError(f"Invalid status '{new_status}'. Valid: {SPEC_STATUS_FLOW}")
        current = spec.get("status", "draft")
        current_idx = SPEC_STATUS_FLOW.index(current)
        new_idx = SPEC_STATUS_FLOW.index(new_status)
        if new_idx < current_idx:
            raise ValueError(f"Cannot move status back from '{current}' to '{new_status}'")
        spec = dict(spec)
        spec["status"] = new_status
        return spec


# ── SprintStatusUpdater ──────────────────────────────────────────────────

class SprintStatusUpdater:
    @staticmethod
    def can_transition(current: str, new_status: str) -> tuple[bool, str]:
        if new_status not in SPRINT_STATUS_FLOW:
            return False, f"Invalid sprint status '{new_status}'. Valid: {SPRINT_STATUS_FLOW}"
        if current not in SPRINT_STATUS_FLOW:
            current = "planning"
        current_idx = SPRINT_STATUS_FLOW.index(current)
        new_idx = SPRINT_STATUS_FLOW.index(new_status)
        if new_idx < current_idx:
            return False, f"Cannot move sprint status back from '{current}' to '{new_status}'"
        if new_idx == current_idx:
            return False, f"Sprint is already '{current}'"
        return True, ""


# ── Helper ────────────────────────────────────────────────────────────────

def _parse_field(data: dict, field: str, default=None):
    val = data.get(field, default)
    if isinstance(val, str):
        val = val.strip()
        if val.startswith(("[", "{")):
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
    return val


# ── SpecService (domain) ─────────────────────────────────────────────────

class SpecService:
    """Operaciones puras de dominio sobre Spec.

    Reemplaza Spec.merge() (eliminado de la entidad por pureza).
    """

    @staticmethod
    def merge(spec: Spec, updates: dict) -> Spec:
        """Refina campos desde un dict (partial update)."""
        data = spec.to_dict()
        data.update({k: v for k, v in updates.items() if v is not None})
        if "status" in updates and updates["status"] is not None:
            data["status"] = updates["status"]
        return Spec.from_dict(data)

    @staticmethod
    def refine(existing: Spec, data: dict) -> Spec:
        """Refina una spec existente con nuevos datos (legacy-compatible)."""
        tc_raw = data.get("task_context", "{}")
        try:
            task_context = json.loads(tc_raw) if isinstance(tc_raw, str) else tc_raw
        except (json.JSONDecodeError, TypeError):
            task_context = existing.to_dict().get("task_context", {})

        merged = {
            "spec_id": existing.spec_id,
            "spring_id": existing.spring_id,
            "objective": data.get("objective", data.get("title", existing.objective)),
            "type": data.get("type", existing.spec_type),
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
            "decisions": _parse_field(data, "decisions", existing.to_dict().get("decisions", [])),
            "loop_verification": _parse_field(data, "loop_verification", existing.to_dict().get("loop_verification", [])),
            "loop_acceptance": data.get("loop_acceptance", existing.to_dict().get("loop_acceptance", "all")),
            "status": "specified" if data.get("result_state") or data.get("task_description") else existing.status,
        }
        return Spec.from_dict(merged)
