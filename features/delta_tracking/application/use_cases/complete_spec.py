"""use_cases/complete_spec.py — CompleteSpecUseCase.

Completar una Spec con commit.
Aplica cuando IMPLEMENT.REPORT_GATE aprueba el resultado.
Establece status=completed y agrega commit_hash, commit_url, completed_at.
"""
from __future__ import annotations

from datetime import datetime, timezone

from shared.common.shared_domain import ExecutionContext
from ._sprint_helpers import _spring_svc, _get_current_spec_id


class CompleteSpecUseCase:
    """Completar una Spec con commit."""

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        if not sprint_dir:
            raise ValueError("chain_dir not available")

        spring_svc = _spring_svc(sprint_dir)
        spring_id = spring_svc.get_spring_id(sprint_dir)
        spec_id = data.get("spec_id") or _get_current_spec_id(sprint_dir)
        if not spec_id:
            raise ValueError("spec_id not available")

        completed_at = data.get("completed_at") or datetime.now(timezone.utc).isoformat()
        updates = {
            "status": "completed",
            "commit_hash": data.get("commit_hash", ""),
            "commit_url": data.get("commit_url", ""),
            "completed_at": completed_at,
        }

        updated = spring_svc.update_spec(spring_id, spec_id, updates)
        if not updated:
            return {"ok": False, "error": f"Spec {spec_id} not found"}

        return {
            "ok": True,
            "spec_id": spec_id,
            "status": "completed",
            "commit_hash": data.get("commit_hash", ""),
            "message": f"Spec {spec_id} completed with commit.",
        }
