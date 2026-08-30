"""domain/values.py — Value Objects del dominio Workflow Navigation.

SSOT de objetos inmutables que representan conceptos del workflow.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetryPolicy:
    """Politica de reintento para sub-estados auto (TASK-009).

    Attributes:
        max_attempts: Maximos reintentos antes de on_failure.
        backoff: Algoritmo de backoff (fixed|linear|exponential|jitter).
        initial_delay_ms: Delay inicial en ms.
        on_error: Mapa de ErrorType -> ErrorStrategy.
            Ej: {"validation": "block+message", "execution": "retry"}
            Si no hay entrada para un tipo, se usa DEFAULT_STRATEGIES.
    """

    max_attempts: int = 3
    backoff: str = "exponential"  # fixed | linear | exponential | jitter
    initial_delay_ms: int = 1000
    on_error: dict[str, str] | None = None
    """
    Mapa de ErrorType -> ErrorStrategy.
    Ej: {"execution": "retry", "validation": "block+message"}
    """


@dataclass(frozen=True)
class SideEffectDef:
    """Efecto secundario declarativo a ejecutar tras un sub-estado.

    Attributes:
        type: Tipo de efecto (index_code | documenting_knowledge | sync_delta |
              emit_event | persist_snapshot | run_script).
        params: Parametros del efecto (dependen del tipo).
        optional: Si True, fallo no bloquea el flujo ni gatilla compensacion.
        compensation: Tipo de compensacion para este efecto.
            Vacio = no tiene compensacion (solo warning log).
        idempotency_key: Key unica para evitar doble ejecucion.
            Si se re-ejecuta con misma key, retorna resultado previo.
    """

    type: str  # index_code | documenting_knowledge | sync_delta | emit_event | persist_snapshot | run_script
    params: dict[str, object] = field(default_factory=dict)
    optional: bool = False
    compensation: str = ""  # fuente única (SPEC-014): la que declara el grafo (remove_knowledge | restore_code_snapshot | ...)
    idempotency_key: str = ""  # Para evitar doble ejecucion


@dataclass(frozen=True)
class SelectOption:
    """Opcion seleccionable para sub-estados user_input."""

    value: str
    label: str = ""
    description: str = ""
    requires: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SubStateKey:
    """Identificador de un sub-estato dentro de un nodo. Inmutable."""

    node_id: str
    sub_key: str

    def __str__(self) -> str:
        return f"{self.node_id}.{self.sub_key}"


@dataclass(frozen=True)
class SubStateData:
    """Datos capturados en un sub-estado particular."""

    fields: dict[str, object] = field(default_factory=dict)
    completed_at: str | None = None

    def is_field_complete(self, field: str) -> bool:
        val = self.fields.get(field)
        return val is not None and val != ""

    def all_required_complete(self, required: list[str]) -> bool:
        return all(self.is_field_complete(f) for f in required)

    def with_field(self, field: str, value: object) -> "SubStateData":
        """Retorna nueva SubStateData con el campo actualizado."""
        new_fields = dict(self.fields)
        new_fields[field] = value
        return SubStateData(fields=new_fields, completed_at=self.completed_at)


@dataclass(frozen=True)
class TimeoutConfig:
    """Config de timeout de un sub-estado (boundary timer, BPMN).

    Compone los campos planos de SubState en un VO (refactor de dominio,
    ADR-WALKER-067): la declaración persiste plana en el grafo; este VO se
    DERIVA para que los servicios consuman un objeto componible.
    """

    timeout_ms: int = 0
    """Max time to wait before auto-resolving (0 = no timeout)."""
    interrupting: bool = True
    """True: el timeout interrumpe el sub-estado (boundary event).
    False: dispara como side-effect sin interrumpir."""
    on_timeout: str = ""
    """Estrategia al expirar: skip | force | escalate | compensate."""

    @property
    def enabled(self) -> bool:
        return self.timeout_ms > 0


@dataclass(frozen=True)
class SignalEvents:
    """Eventos de señal de un sub-estado (signal events, Temporal/BPMN).

    Compone wait_for/emits/waits_for/on_signal (planos en SubState) en un VO.
    """

    wait_for: list[str] = field(default_factory=list)
    """Eventos que pueden reanudar este wait state (event-based gateway)."""
    emits: list[str] = field(default_factory=list)
    """Señales emitidas al completar el sub-estado."""
    waits_for: list[str] = field(default_factory=list)
    """Señales que reanudan este wait state antes del timeout."""
    on_signal: str = ""
    """Acción al llegar la señal: auto_advance | skip | force | escalate."""

    @property
    def has_events(self) -> bool:
        return bool(self.wait_for or self.waits_for or self.emits)


__all__ = [
    "RetryPolicy",
    "SideEffectDef",
    "SelectOption",
    "SubStateKey",
    "SubStateData",
    "TimeoutConfig",
    "SignalEvents",
]
