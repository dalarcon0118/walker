"""
domain/services.py — CKG domain services (pure).

CKG_STATUS_FLOW: tracks whether CKG knowledge is up-to-date
  relative to the current specification's implementation.
"""
from __future__ import annotations

CKG_STATUS_FLOW = ["up_to_date", "pending_update", "stale"]


class CkgStatusUpdater:
    """Updates CKG status based on workflow events.

    Flow:
      up_to_date → pending_update → up_to_date
      (stable)      (work starts)    (knowledge captured)

      stale → up_to_date
      (code changed)  (re-indexed)

    PURE: No IO. Receives current status and event, returns new status.
    """

    VALID_TRANSITIONS = {
        "up_to_date": ["pending_update"],
        "pending_update": ["up_to_date"],
        "stale": ["up_to_date"],
    }

    @staticmethod
    def on_implementation_start(current: str) -> str:
        """Called when a spec enters implementation (IMPLEMENT.WORK)."""
        return CkgStatusUpdater._transition(current, "pending_update")

    @staticmethod
    def on_knowledge_captured(current: str) -> str:
        """Called when knowledge is saved (save_to_ckg, collect_knowledge)."""
        return CkgStatusUpdater._transition(current, "up_to_date")

    @staticmethod
    def on_code_indexed(current: str) -> str:
        """Called after CPG rebuild / code index update."""
        return CkgStatusUpdater._transition(current, "up_to_date")

    @staticmethod
    def _transition(current: str, target: str) -> str:
        valid = CkgStatusUpdater.VALID_TRANSITIONS.get(current, [])
        if target not in valid:
            raise ValueError(
                f"CKG status cannot transition from '{current}' to '{target}'. "
                f"Valid targets: {valid}"
            )
        return target

    @staticmethod
    def is_up_to_date(status: str) -> bool:
        return status == "up_to_date"
