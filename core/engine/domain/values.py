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
        type: Tipo de efecto (update_cpg | save_ckg | update_delta_graph |
              emit_event | persist_snapshot | run_script).
        params: Parametros del efecto (dependen del tipo).
        optional: Si True, fallo no bloquea el flujo ni gatilla compensacion.
        compensation: Tipo de compensacion para este efecto.
            Vacio = no tiene compensacion (solo warning log).
        idempotency_key: Key unica para evitar doble ejecucion.
            Si se re-ejecuta con misma key, retorna resultado previo.
    """

    type: str  # update_cpg | save_ckg | update_delta_graph | emit_event | persist_snapshot | run_script
    params: dict[str, object] = field(default_factory=dict)
    optional: bool = False
    compensation: str = ""  # delete_ckg_node | restore_cpg_snapshot | rollback_ckg | restore_delta_graph
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


__all__ = [
    "RetryPolicy",
    "SideEffectDef",
    "SelectOption",
    "SubStateKey",
    "SubStateData",
]
