"""application/ports/cpg_repository.py — Interfaz del repositorio CPG.

Puerto de salida en Clean Architecture. Define el contrato de persistencia
y acceso al Code Property Graph. La infraestructura lo implementa via
CpgFileRepository (archivos) o CognitiveGraph (indice).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class CpgRepository(ABC):
    """Contrato de persistencia y acceso al CPG.

    ADR-WALKER-063 (CA-9): única implementación = CognitiveCpgRepository
    (puerto StorageBackend; sqlite v2 SSOT). CpgFileRepository (lector
    directo .json.gz) fue eliminado.
    """

    @abstractmethod
    def load(self, project_id: str) -> dict:
        """Carga el CPG completo (nodos, edges, stats, metadata)."""
        ...

    @abstractmethod
    def save(self, data: dict) -> None:
        """Persiste el CPG completo."""
        ...

    @abstractmethod
    def search_nodes(
        self,
        filters: dict,
        max_results: int = 50,
        offset: int = 0,
    ) -> dict:
        """Busca nodos en el CPG por filtros estructurados."""
        ...

    @abstractmethod
    def get_node(self, node_id: str) -> dict | None:
        """Obtiene un nodo por ID."""
        ...

    @abstractmethod
    def get_stats(self, project_id: str) -> dict:
        """Estadisticas del CPG (total_nodes, total_edges, etc.)."""
        ...
