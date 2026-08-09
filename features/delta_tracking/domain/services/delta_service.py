"""domain/services/delta_service.py — Puertos de servicios de dominio Delta.

Define el contrato de los servicios de negocio del DeltaGraph:
- IDeltaService: operaciones principales del grafo delta
- ISprintService: operaciones específicas de sprints
- ISpecService: operaciones específicas de specs
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class IDeltaService(ABC):
    """Contrato para operaciones principales del DeltaGraph."""

    @abstractmethod
    def create_spring(self, goal: str, project_id: str = "", **kwargs) -> dict[str, Any]:
        """Crea un nuevo spring/sprint."""
        ...

    @abstractmethod
    def get_active_spring(self) -> dict[str, Any] | None:
        """Obtiene el spring activo."""
        ...

    @abstractmethod
    def capture_task(self, task_data: dict) -> dict[str, Any]:
        """Captura una tarea desde contexto y la agrega como spec."""
        ...

    @abstractmethod
    def get_context(self, spring_id: str = "") -> dict[str, Any]:
        """Obtiene el contexto del sprint activo o uno específico."""
        ...


class ISprintService(ABC):
    """Contrato para operaciones de sprints."""

    @abstractmethod
    def list_springs(self, status: str = "") -> list[dict[str, Any]]:
        """Lista sprints, opcionalmente filtrados por status."""
        ...

    @abstractmethod
    def update_spring(self, spring_id: str, changes: dict) -> dict[str, Any]:
        """Actualiza un sprint existente."""
        ...

    @abstractmethod
    def activate_spring(self, spring_id: str) -> dict[str, Any]:
        """Activa un sprint."""
        ...

    @abstractmethod
    def close_spring(self, spring_id: str) -> dict[str, Any]:
        """Cierra un sprint."""
        ...


class ISpecService(ABC):
    """Contrato para operaciones de specs."""

    @abstractmethod
    def create_spec(self, spring_id: str, objective: str, **kwargs) -> dict[str, Any]:
        """Crea una nueva spec en un sprint."""
        ...

    @abstractmethod
    def get_specs(self, spring_id: str, status: str = "") -> list[dict[str, Any]]:
        """Obtiene las specs de un sprint."""
        ...

    @abstractmethod
    def update_spec(self, spring_id: str, spec_id: str, changes: dict) -> dict[str, Any]:
        """Actualiza una spec."""
        ...

    @abstractmethod
    def complete_spec(self, spring_id: str, spec_id: str, result_state: str = "") -> dict[str, Any]:
        """Completa una spec."""
        ...
