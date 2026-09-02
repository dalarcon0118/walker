"""domain/conflict.py — Conflict entities and resolution.

Represents workflow states that require intervention and how
they're resolved. Part of delta_tracking domain because conflicts
are associated with specs/sprints, not the workflow engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class ResolutionStrategy:
    """Strategy for resolving a conflict."""

    strategy: str  # skip | force | edit | merge | compensate
    payload: dict = field(default_factory=dict)
    applied_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    applied_by: str = ""


@dataclass(frozen=True)
class Conflict:
    """Conflict detected in a sprint/workflow.

    Types:
    - missing_required: required field missing in sub-state
    - invalid_transition: transition not allowed by preconditions
    - gate_blocked: gate blocked by external validation
    - obsolete_spec: obsolete or duplicate spec
    - spec_overlap: specs overlapping/modifying the same thing
    - state_corruption: workflow state reconstructed or inconsistent
    """

    type: str
    node: str = ""
    sub_state: str = ""
    spec_id: str = ""
    message: str = ""
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    strategy: str = ""
    resolution: ResolutionStrategy | None = None
    resolved: bool = False
    resolved_at: str = ""
    metadata: dict = field(default_factory=dict)

    def with_resolution(self, strategy: ResolutionStrategy) -> Conflict:
        return Conflict(
            type=self.type,
            node=self.node,
            sub_state=self.sub_state,
            spec_id=self.spec_id,
            message=self.message,
            detected_at=self.detected_at,
            strategy=strategy.strategy,
            resolution=strategy,
            resolved=True,
            resolved_at=strategy.applied_at,
            metadata=self.metadata,
        )
