"""infrastructure/services/business_analyzer.py — Implementa IBusinessAnalyzer via CognitiveGraph.

Capa de infraestructura. Orquesta:
  1. CpgRepository → obtiene nodes/edges del índice 'cpg'
  2. BusinessAnalyzer (dominio puro) → produce pure_functions, flows, patterns
"""

from __future__ import annotations

from features.code_analysis.application.ports.cpg_repository import CpgRepository
from features.code_analysis.domain.services.analysis_service import IBusinessAnalyzer
from features.code_analysis.domain.services.business_analyzer import analyze


class CognitiveBusinessAnalyzer(IBusinessAnalyzer):
    """Implementación de IBusinessAnalyzer sobre CognitiveGraph."""

    def __init__(self, repo: CpgRepository):
        self._repo = repo

    def analyze(self, scope: dict | None = None) -> dict[str, object]:
        data = self._repo.load()
        nodes = data.get("nodes", {})
        edges = data.get("edges", [])

        if scope:
            nodes = self._filter_by_scope(nodes, scope)

        return analyze(nodes, edges)

    @staticmethod
    def _filter_by_scope(nodes: dict[str, dict], scope: dict) -> dict[str, dict]:
        file_filter = scope.get("file") or scope.get("path") or scope.get("area") or ""
        type_filter = scope.get("type") or scope.get("kind") or ""

        result = {}
        for nid, node in nodes.items():
            if not isinstance(node, dict):
                continue
            if file_filter and file_filter not in node.get("path", ""):
                continue
            if type_filter and type_filter != node.get("type", ""):
                continue
            result[nid] = node
        return result
