"""application/ports/ckg_repository.py — Interfaz del repositorio CKG.

Puerto de salida en Clean Architecture. Define el contrato de persistencia
y consulta del Knowledge Graph. La infraestructura lo implementa via
CognitiveGraph (indice 'ckg').
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class CkgRepository(ABC):
    """Contrato de persistencia y consulta del CKG."""

    @abstractmethod
    def save_nodes(self, nodes: list[dict]) -> dict:
        """Persiste nodos en batch via store_many. Retorna {ok, node_ids, degraded}."""
        ...

    @abstractmethod
    def query(self, filters: dict | None = None) -> list[dict]:
        """Consulta nodos activos del CKG (excluye superseded)."""
        ...

    @abstractmethod
    def search(self, query: str, max_results: int = 20) -> list[dict]:
        """Busqueda fulltext en el CKG."""
        ...

    @abstractmethod
    def delete_by_metadata(self, key: str, value: str) -> dict:
        """Elimina nodos por metadata en batch."""
        ...

    @abstractmethod
    def next_id(self, prefix: str = "node") -> str:
        """Genera ID secuencial sin colision (usa max_key)."""
        ...
