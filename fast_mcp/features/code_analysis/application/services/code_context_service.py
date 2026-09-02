"""application/services/code_context_service.py — CodeContextService.

Application Service for structured code context. Orchestrates QueryGraphUseCase
+ CodeContextUseCase to produce the business value model.

Does NOT expose: nodes, edges, CPG, graph internals.
"""
from __future__ import annotations

from typing import Any

from fast_mcp.features.code_analysis.application.query_service import QueryGraphUseCase
from fast_mcp.features.code_analysis.application.use_cases.code_context import CodeContextUseCase
from fast_mcp.shared_kernel.project_support import ProjectService


class CodeContextService:
    """Orchestrates structured code context from CPG."""

    def __init__(self, chain_dir: str, project_id: str = ""):
        self._chain_dir = chain_dir
        self._project_id = project_id

    def execute(self, target_file: str = "", query: str = "") -> dict[str, Any]:
        """Builds structured code context.

        Args:
            target_file: file path for file-level context.
            query: text query for symbol/function/class search.

        Returns:
            Value model dict with context_type, target, confidence, summary,
            symbols, relations, insights, next_actions.
        """
        paths = ProjectService.resolve_paths(self._chain_dir)
        project_id = paths.project_id or self._project_id

        use_case = QueryGraphUseCase(
            chain_dir=self._chain_dir,
            project_id=project_id,
        )
        graph = use_case.graph
        context_uc = CodeContextUseCase(use_case, graph)

        if target_file:
            return context_uc.for_file(target_file)
        if query:
            return context_uc.for_query(query)
        return {
            "context_type": "search",
            "target": "",
            "confidence": "low",
            "summary": "Empty context: no file or query provided.",
            "symbols": [],
            "relations": {"calls": [], "called_by": [], "imports": []},
            "insights": ["Provide file or query to search."],
            "next_actions": [],
        }
