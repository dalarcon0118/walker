"""
domain/services.py — Pure domain services for sprint planning.

PRINCIPIO: These are PURE functions. No IO, no infrastructure imports.
SSOT: Specification field definitions, validation rules, default values.

NOTE: "Specification" refers to work items in plan.json (sprint backlog).
      This is distinct from "workflow tasks" which are the actions
      registered in the TaskRegistry.
"""
from __future__ import annotations

import json
from typing import Any


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
"""Valid statuses for a specification. Flow: draft → specified → in_progress → completed."""

SPRINT_STATUS_FLOW = ["planning", "active", "reviewing", "closed"]
"""Valid statuses for a sprint. Flow: planning → active → reviewing → closed."""


# ── SpecValidator ─────────────────────────────────────────────────────────

class SpecValidator:
    """Validates specification fields.

    PURE: No IO. Receives data, returns errors.
    """

    @staticmethod
    def validate_fields(data: dict) -> list[str]:
        """Validate required fields in submitted data. Returns errors."""
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
        """Validate a complete specification dict from plan.json."""
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
    """Builds specification dicts for plan.json.

    PURE: Receives data, returns dicts.
    """

    @staticmethod
    def create_default(index: int, data: dict) -> dict:
        """Create a default specification from minimal input (title + type + domain)."""
        return {
            "spec_id": data.get("spec_id", data.get("task_id", f"SPEC-{index + 1:03d}")),
            "objective": data.get("title", data.get("objective", "")),
            **{k: v for k, v in SPEC_DEFAULTS.items() if k != "type"},
            "type": "feature",
        }

    @staticmethod
    def create_from_plan(index: int, plan_item: dict) -> dict:
        """Create a spec from sprint planning input (objective, type, domain)."""
        return {
            "spec_id": f"SPEC-{index + 1:03d}",
            "objective": plan_item.get("objective", ""),
            "type": plan_item.get("type", "feature"),
            "domain": plan_item.get("domain", ""),
            **{k: v for k, v in SPEC_DEFAULTS.items() if k != "domain" and k != "type"},
        }

    @staticmethod
    def refine(existing: dict, data: dict) -> dict:
        """Refine an existing specification with new field values.

        Sets status to 'specified' if result_state or task_description is provided.
        """
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


# ── SprintPlanBuilder ─────────────────────────────────────────────────────

class SprintPlanBuilder:
    """Builds sprint plan and initial state structures. PURE."""

    @staticmethod
    def build_plan(sprint_id: str, goal: str, specs_raw: list) -> dict:
        """Build the complete plan.json structure with specifications."""
        plan_specs = [SpecBuilder.create_from_plan(i, s) for i, s in enumerate(specs_raw)]
        return {
            "version": "2.0",
            "chain_id": sprint_id,
            "goal": goal,
            "workflow": "workflow.v3/",
            "specifications": plan_specs,
        }

    @staticmethod
    def build_initial_state(sprint_dir: str, sprint_id: str, goal: str,
                            project_id: str, session_id: str, specs: list) -> dict:
        """Build the initial sprint-state.json structure."""
        return {
            "version": "5.0",
            "current_node": "SPRINT", "current_sub_state": "ACTIVE",
            "sub_state_index": 0, "sub_state_data": {}, "history": [],
            "context": {
                "chain_dir": sprint_dir, "project_id": project_id,
                "current_spec_index": 0, "session_id": session_id,
            },
            "updated_at": __import__("datetime").datetime.now().isoformat(),
            "sprint": {
                "chain_id": sprint_id, "goal": goal, "project_id": project_id,
                "status": "planning",
            },
            "specifications": specs,
            "spec_order": [s["spec_id"] for s in specs],
        }


# ── SpecStatusUpdater ────────────────────────────────────────────────────

class SpecStatusUpdater:
    """Updates specification status according to the status flow.

    Flow: draft → specified → in_progress → completed
    PURE: No IO. Receives spec and new status, returns updated spec.
    """

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
    """Updates sprint status according to the flow.

    Flow: planning → active → reviewing → closed
    PURE: No IO. Receives current status and new status, validates.
    """

    @staticmethod
    def can_transition(current: str, new_status: str) -> tuple[bool, str]:
        """Check if a sprint status transition is valid.

        Returns (allowed, reason). If allowed, reason is empty.
        """
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
