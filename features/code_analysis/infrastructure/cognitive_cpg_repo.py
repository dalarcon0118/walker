"""infrastructure/cognitive_cpg_repo.py — Implementa CpgRepository via CognitiveGraph.

Capa de infraestructura. Delega en el indice 'cpg' de CognitiveGraph
para busquedas estructuradas y en CpgFileRepository para el storage completo.
"""
from __future__ import annotations

from features.code_analysis.application.ports.cpg_repository import CpgRepository
from features.code_analysis.infrastructure.repository import CpgFileRepository


class CognitiveCpgRepository(CpgRepository):
    """Implementacion sobre CognitiveGraph + CpgFileRepository."""

    def __init__(self, data_dir: str, project_id: str):
        self._repo = CpgFileRepository(data_dir)
        self._project_id = project_id

    def load(self, project_id: str = "") -> dict:
        return self._repo.load(project_id or self._project_id)

    def save(self, data: dict) -> None:
        self._repo.save(data)

    def search_nodes(
        self, filters: dict, max_results: int = 50, offset: int = 0,
    ) -> dict:
        from libs.cognitive_graph import CognitiveGraph

        cg = CognitiveGraph(self._repo.data_dir, auto_register=True)
        query = dict(filters)
        if "size" not in query:
            query["size"] = max_results
        if offset:
            query["_from"] = offset
        return cg.search("cpg", query)

    def get_node(self, node_id: str) -> dict | None:
        from libs.cognitive_graph import CognitiveGraph

        cg = CognitiveGraph(self._repo.data_dir, auto_register=True)
        result = cg.search("cpg", {"_id": node_id, "size": 1})
        hits = result.get("hits", {}).get("hits", [])
        return hits[0].get("_source") if hits else None

    def get_stats(self, project_id: str = "") -> dict:
        data = self.load(project_id or self._project_id)
        nodes = data.get("nodes", {})
        return {
            "total_nodes": len(nodes),
            "total_edges": len(data.get("edges", [])),
            "files_scanned": len(set(
                n.get("path", "") for n in nodes.values()
                if isinstance(n, dict)
            )),
        }
