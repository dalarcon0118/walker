"""domain/conflict.py — Entidades de conflicto y resolución.

Representan estados del workflow que requieren intervención y cómo
se resuelven. Son parte del dominio de delta_tracking porque los
conflictos están asociados a specs/sprints, no al workflow engine.

Nota (ADR-051 G5): vive en domain/ (no domain/entities/) porque
`entities.py` (módulo con Spec/Spring) sombrea al directorio `entities/`
— la estructura entities/ se completará en la migración del ADR-048.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class ResolutionStrategy:
    """Estrategia de resolución para un conflicto."""
    strategy: str  # skip | force | edit | merge | compensate
    payload: dict = field(default_factory=dict)
    applied_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    applied_by: str = ""


@dataclass(frozen=True)
class Conflict:
    """Conflicto detectado en un sprint/workflow.

    Tipos:
    - missing_required: campo requerido faltante en sub-estado
    - invalid_transition: transición no permitida por precondiciones
    - gate_blocked: gate bloqueada por validación externa (script, required_tools)
    - obsolete_spec: spec obsoleta o duplicada
    - spec_overlap: specs que se superponen/modifican lo mismo
    - state_corruption: estado del workflow reconstruido o inconsistente
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

    def with_resolution(self, strategy: ResolutionStrategy) -> "Conflict":
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
