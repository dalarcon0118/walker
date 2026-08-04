"""application/ports/delta_repository.py — Interfaz del repositorio Delta.

Puerto de salida (driven port) en Clean Architecture. Define el contrato
que la capa de infraestructura implementa. La capa de aplicacion depende
de esta abstraccion, no de CognitiveGraph directamente.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from features.delta_tracking.domain.entities import Spring, Spec


class DeltaRepository(ABC):
    """Contrato de persistencia para el DeltaGraph."""

    # ── Spring ──

    @abstractmethod
    def get_spring(self, spring_id: str) -> Spring | None:
        """Obtiene un spring por ID."""
        ...

    @abstractmethod
    def save_spring(self, spring: Spring) -> None:
        """Persiste un spring (create o update)."""
        ...

    @abstractmethod
    def list_springs(self, status: str = "") -> list[Spring]:
        """Lista springs, opcionalmente filtrados por status."""
        ...

    @abstractmethod
    def get_active_spring(self) -> Spring | None:
        """Obtiene el spring activo (status=active, active=True)."""
        ...

    # ── Spec ──

    @abstractmethod
    def get_specs(self, spring_id: str, status: str = "") -> list[Spec]:
        """Obtiene todas las specs de un spring."""
        ...

    @abstractmethod
    def get_spec(self, spring_id: str, spec_id: str) -> Spec | None:
        """Obtiene una spec por spring_id + spec_id."""
        ...

    @abstractmethod
    def save_spec(self, spec: Spec) -> None:
        """Persiste una spec (create o update)."""
        ...

    @abstractmethod
    def save_specs_batch(self, specs: list[Spec]) -> None:
        """Persiste multiples specs en batch (1 store_many)."""
        ...

    @abstractmethod
    def delete_spec(self, spring_id: str, spec_id: str) -> None:
        """Elimina una spec."""
        ...

    # ── Relaciones ──

    @abstractmethod
    def add_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        """Agrega un edge entre dos nodos."""
        ...
