"""application/use_cases/code_context.py — CodeContextUseCase.

Pure use case: receives graph + query parameters, returns structured context.
No infrastructure, no IO. Business logic only.
"""
from __future__ import annotations

from typing import Any

from fast_mcp.features.code_analysis.application.query_service import QueryGraphUseCase
from fast_mcp.features.code_analysis.domain.graph import Graph


class CodeContextUseCase:
    """Builds structured code context from CPG graph."""

    def __init__(self, use_case: QueryGraphUseCase, graph: Any):
        self._use_case = use_case
        self._graph = graph

    def for_file(self, target_file: str) -> dict[str, Any]:
        """Structured context for a specific file."""
        symbols: list[dict[str, Any]] = []
        relations = {"calls": [], "called_by": [], "imports": []}

        # File structure via read
        read_result = self._use_case.read(target_file)
        if read_result and isinstance(read_result, dict):
            file_symbols = read_result.get("symbols", [])
            symbols = [
                {
                    "name": s.get("name", ""),
                    "type": s.get("type", ""),
                    "file": target_file,
                    "line": s.get("line"),
                    "signature": s.get("name", ""),
                }
                for s in file_symbols
                if s.get("name")
            ]

        # Relations via graph
        file_nodes = [
            n for n in self._graph.nodes.values()
            if hasattr(n, "file") and target_file in (n.file or "")
        ]
        relations = self._build_relations(file_nodes)

        return self._build_output(
            context_type="file",
            target=target_file,
            symbols=symbols,
            relations=relations,
        )

    def for_query(self, query: str) -> dict[str, Any]:
        """Structured context for a symbol/function/class search."""
        grep_result = self._use_case.grep(query)
        matches = grep_result.get("matches", [])[:50]

        symbols = [
            {
                "name": m.get("name", ""),
                "type": m.get("type", ""),
                "file": m.get("file", ""),
                "line": m.get("line"),
                "signature": m.get("signature", "") or m.get("name", ""),
            }
            for m in matches
            if m.get("name")
        ]

        return self._build_output(
            context_type="search",
            target=query,
            symbols=symbols,
            relations={"calls": [], "called_by": [], "imports": []},
        )

    def _build_relations(self, target_nodes: list[Any]) -> dict[str, list[dict]]:
        """Builds business relations from graph edges."""
        relations: dict[str, list[dict]] = {
            "calls": [],
            "called_by": [],
            "imports": [],
        }
        if not target_nodes or not self._graph:
            return relations

        target_ids = {n.id for n in target_nodes if hasattr(n, "id")}
        for edge in self._graph.edges:
            e = edge if isinstance(edge, dict) else edge.__dict__
            etype = (e.get("type") or getattr(edge, "type", "") or "").lower()
            from_id = e.get("from") or getattr(edge, "from_id", "")
            to_id = e.get("to") or getattr(edge, "to_id", "")

            if etype == "calls":
                if from_id in target_ids:
                    to_node = self._graph.nodes.get(to_id)
                    if to_node:
                        relations["calls"].append({
                            "name": getattr(to_node, "name", ""),
                            "file": getattr(to_node, "file", ""),
                            "type": getattr(to_node, "type", ""),
                        })
                if to_id in target_ids:
                    from_node = self._graph.nodes.get(from_id)
                    if from_node:
                        relations["called_by"].append({
                            "name": getattr(from_node, "name", ""),
                            "file": getattr(from_node, "file", ""),
                            "type": getattr(from_node, "type", ""),
                        })
            elif etype == "imports":
                if from_id in target_ids:
                    to_node = self._graph.nodes.get(to_id)
                    if to_node:
                        relations["imports"].append({
                            "name": getattr(to_node, "name", ""),
                            "file": getattr(to_node, "file", ""),
                        })

        # Dedup
        for key in relations:
            seen = set()
            deduped = []
            for item in relations[key]:
                k = (item.get("name"), item.get("file"))
                if k not in seen:
                    seen.add(k)
                    deduped.append(item)
            relations[key] = deduped

        return relations

    def _build_output(
        self,
        context_type: str,
        target: str,
        symbols: list[dict[str, Any]],
        relations: dict[str, list[dict]],
    ) -> dict[str, Any]:
        """Builds the final value model."""
        confidence = self._confidence(symbols, relations)
        summary = (
            f"{len(symbols)} entities found for '{target}'."
            if symbols
            else f"Empty context for '{target}'."
        )
        insights = self._insights(symbols, relations)
        next_actions = self._next_actions(context_type, symbols, relations)

        return {
            "context_type": context_type,
            "target": target,
            "confidence": confidence,
            "summary": summary,
            "symbols": symbols[:100],
            "relations": relations,
            "insights": insights,
            "next_actions": next_actions,
        }

    def _confidence(self, symbols: list[dict], relations: dict[str, list[dict]]) -> str:
        if not symbols and not any(relations.values()):
            return "low"
        if symbols and any(relations.values()):
            return "high"
        return "medium"

    def _insights(self, symbols: list[dict], relations: dict[str, list[dict]]) -> list[str]:
        insights: list[str] = []
        if not symbols:
            return ["No indexed symbols found for this context."]

        if relations["calls"]:
            insights.append(f"Makes {len(relations['calls'])} external calls.")
        if relations["called_by"]:
            insights.append(f"Called from {len(relations['called_by'])} places.")
        if relations["imports"]:
            insights.append(f"Imports {len(relations['imports'])} dependencies.")
        return insights

    def _next_actions(self, context_type: str, symbols: list[dict], relations: dict[str, list[dict]]) -> list[str]:
        actions: list[str] = []
        if context_type == "file" and not symbols:
            actions.append("Run build_code_index to update the CPG.")
        if relations["called_by"]:
            actions.append("Review callers to validate change impact.")
        if relations["imports"]:
            actions.append("Verify external dependencies in imports.")
        return actions
