"""domain/values.py — Value Objects for the workflow domain.

Immutable objects representing workflow concepts.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetryPolicy:
    """Retry policy for auto sub-states."""
    max_attempts: int = 3
    backoff: str = "exponential"  # fixed | linear | exponential | jitter
    initial_delay_ms: int = 1000
    on_error: dict[str, str] | None = None
    """Map of ErrorType -> ErrorStrategy.
    Ej: {'execution': 'retry', 'validation': 'block+message'}
    If no entry for a type, DEFAULT_STRATEGIES applies."""


@dataclass(frozen=True)
class SideEffectDef:
    """Declarative side effect to execute after a sub-state."""
    type: str  # index_code | documenting_knowledge | sync_delta | emit_event | run_script
    params: dict[str, object] = field(default_factory=dict)
    optional: bool = False
    compensation: str = ""  # from graph declaration (SPEC-014)
    idempotency_key: str = ""


@dataclass(frozen=True)
class SelectOption:
    """Selectable option for user_input sub-states."""
    value: str
    label: str = ""
    description: str = ""
    requires: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SubStateKey:
    """Immutable identifier for a sub-state within a node."""
    node_id: str
    sub_key: str

    def __str__(self) -> str:
        return f"{self.node_id}.{self.sub_key}"


@dataclass(frozen=True)
class SubStateData:
    """Data captured in a particular sub-state."""
    fields: dict[str, object] = field(default_factory=dict)
    completed_at: str | None = None

    def is_field_complete(self, field: str) -> bool:
        """¿El campo está declarado y tiene contenido?

        Un contenedor vacío NO cuenta. Antes `[] != ""` daba True, así que
        `required_fields: ["preconditions"]` se satisfacía con una lista vacía:
        el gate existía y no bloqueaba nada (medido 2026-11-17, ningún sub-estado
        usaba `required_fields`, pero al declararlos el agujero habría anulado el
        gate).
        """
        if field not in self.fields:
            return False
        val = self.fields.get(field)
        if val is None or val == "":
            return False
        if isinstance(val, (list, tuple, set, dict)) and len(val) == 0:
            return False
        return True

    def all_required_complete(self, required: list[str]) -> bool:
        return all(self.is_field_complete(f) for f in required)

    def with_field(self, field: str, value: object) -> "SubStateData":
        """Return new SubStateData with the field updated."""
        new_fields = dict(self.fields)
        new_fields[field] = value
        return SubStateData(fields=new_fields, completed_at=self.completed_at)


@dataclass(frozen=True)
class TimeoutConfig:
    """Timeout config for a sub-state (boundary timer, BPMN)."""
    timeout_ms: int = 0
    interrupting: bool = True
    on_timeout: str = ""  # skip | force | escalate | compensate

    @property
    def enabled(self) -> bool:
        return self.timeout_ms > 0


__all__ = [
    "RetryPolicy",
    "SideEffectDef",
    "SelectOption",
    "SubStateKey",
    "SubStateData",
    "TimeoutConfig",
]
