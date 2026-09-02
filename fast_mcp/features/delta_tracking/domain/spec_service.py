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


# ── SSOT: specification field definitions ──────────────────────────────────

SPEC_REQUIRED_FIELDS = [
    "result_state",
    "task_description",
    "predicates",
    "files",
    "preconditions",
    "acceptance_criteria",
    "quality_gates",
]

SPEC_STATUS_FLOW = ["draft", "specified", "approved", "in_progress", "in_review", "completed"]
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
        # IDEMPOTENCY: same status = successful no-op
        return True, ""


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
        return Spec.from_dict(data)

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
