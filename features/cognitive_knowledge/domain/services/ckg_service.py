"""domain/services/ckg_service.py — Puertos de servicios de dominio CKG.

Define el contrato de los servicios de negocio del CKG:
- ICkgService: operaciones principales del grafo de conocimiento
- IKnowledgeQueryService: búsquedas especializadas
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class ICkgService(ABC):
    """Contrato para operaciones principales del CKG."""

    @abstractmethod
    def save_knowledge(self, nodes: list[dict], project_id: str = "") -> dict[str, Any]:
        """Guarda nodos de conocimiento en el CKG."""
        ...

    @abstractmethod
    def search_knowledge(self, query: str, max_results: int = 20) -> list[dict[str, Any]]:
        """Busca conocimiento por intención."""
        ...

    @abstractmethod
    def get_active_knowledge(self, type_name: str = "") -> list[dict[str, Any]]:
        """Obtiene nodos activos (no superseded)."""
        ...

    @abstractmethod
    def supersede_decision(self, old_node_id: str, new_node_id: str, statement: str = "") -> dict[str, Any]:
        """Marca una decisión como superseded."""
        ...

    @abstractmethod
    def get_knowledge_for_files(self, files: list[str], depth: int = 2) -> list[dict[str, Any]]:
        """Obtiene conocimiento relevante para archivos específicos."""
        ...


class IKnowledgeQueryService(ABC):
    """Contrato para búsquedas especializadas de conocimiento."""

    @abstractmethod
    def search_by_type(self, type_name: str) -> list[dict[str, Any]]:
        """Busca nodos por tipo."""
        ...

    @abstractmethod
    def search_by_tag(self, tag: str) -> list[dict[str, Any]]:
        """Busca nodos por tag."""
        ...

    @abstractmethod
    def get_decisions_about(self, subject: str) -> list[dict[str, Any]]:
        """Obtiene decisiones sobre un tema."""
        ...

    @abstractmethod
    def get_supersedes_chain(self, node_id: str) -> list[dict[str, Any]]:
        """Obtiene la cadena de supersedes de un nodo."""
        ...
