"""application/ports/delta_repository.py — DeltaRepository interface.

Driven port in Clean Architecture. Defines the contract that
the infrastructure layer implements. Application layer depends
on this abstraction, not on CognitiveGraph directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from fast_mcp.features.delta_tracking.domain.entities import Spring, Spec


class DeltaRepository(ABC):
    """Persistence contract for the DeltaGraph."""

    # ── Spring ──

    @abstractmethod
    def get_spring(self, spring_id: str) -> Spring | None:
        """Get a spring by ID."""
        ...

    @abstractmethod
    def save_spring(self, spring: Spring) -> None:
        """Persist a spring (create or update)."""
        ...

    @abstractmethod
    def list_springs(self, status: str = "") -> list[Spring]:
        """List springs, optionally filtered by status."""
        ...

    @abstractmethod
    def get_active_spring(self) -> Spring | None:
        """Get the active spring (status=active, active=True)."""
        ...

    # ── Spec ──

    @abstractmethod
    def get_specs(self, spring_id: str, status: str = "") -> list[Spec]:
        """Get all specs for a spring."""
        ...

    @abstractmethod
    def get_spec(self, spring_id: str, spec_id: str) -> Spec | None:
        """Get a spec by spring_id + spec_id."""
        ...

    @abstractmethod
    def save_spec(self, spec: Spec) -> None:
        """Persist a spec (create or update)."""
        ...

    @abstractmethod
    def save_specs_batch(self, specs: list[Spec]) -> None:
        """Persist multiple specs in batch."""
        ...

    @abstractmethod
    def delete_spec(self, spring_id: str, spec_id: str) -> None:
        """Delete a spec."""
        ...

    # ── Relations ──

    @abstractmethod
    def add_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        """Add an edge between two nodes."""
        ...
