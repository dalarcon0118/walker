"""application/query_service.py — QueryGraphUseCase.

Entry point for querying the CPG: grep, read, structured access.
"""

from __future__ import annotations
import os

from cpg.domain.graph import Graph
from cpg.domain.query import Query
from cpg.infrastructure.repository import CpgFileRepository
from cpg.infrastructure.filters import find_nodes


class QueryGraphUseCase:
    """Query the CPG graph: grep nodes, read file structure, traverse relations."""

    def __init__(
        self, chain_dir: str, project_id: str = "", data_dir: str | None = None
    ):
        self.chain_dir = chain_dir
        self._project_id = project_id
        self._data_dir = data_dir
        self._graph: Graph | None = None
        self._repo: CpgFileRepository | None = None

    @property
    def repo(self) -> CpgFileRepository:
        if self._repo is None:
            from _walker.shared.path_resolver import resolve_project_root

            if self._data_dir:
                data = self._data_dir
            else:
                project_root = resolve_project_root(self.chain_dir)
                data = os.path.join(project_root, "data")
            self._repo = CpgFileRepository(data)
        return self._repo

    @property
    def graph(self) -> Graph:
        if self._graph is None:
            self._graph = self.repo.load_graph(self._project_id)
        return self._graph

    def clear_cache(self) -> None:
        self._graph = None

    def grep(self, query_text: str) -> dict:
        """Execute a grep-like query against the CPG.

        Supports:
          bug:unused_functions     → unused function detection
          bug:dead_branches        → dead branch detection
          security:sqli            → security scan
          function:name file:path  → attribute search
          trace:funcA->funcB       → call path
          calls:target             → relation search
        """
        query = Query.parse(query_text)
        # Route to specialized handlers
        bug_result = self._handle_bugs(query)
        if bug_result:
            return bug_result
        security_result = self._handle_security(query)
        if security_result:
            return security_result
        trace_result = self._handle_trace(query)
        if trace_result:
            return trace_result
        relation_result = self._handle_relations(query)
        if relation_result:
            return relation_result
        # Default: attribute/text search
        matched = find_nodes(self.graph, query.filters, query.free_text)
        return {
            "query": query_text,
            "total": len(matched),
            "matches": matched[:50],
            "truncated": len(matched) > 50,
        }

    def _handle_bugs(self, query: Query) -> dict | None:
        """Handle bug:* queries."""
        from cpg.application.bug_service import ScanBugsUseCase

        scanner = ScanBugsUseCase(self.chain_dir, self.repo, self.graph)
        return scanner.handle_bugs(query)

    def _handle_security(self, query: Query) -> dict | None:
        """Handle security:* queries."""
        from cpg.application.bug_service import ScanBugsUseCase

        scanner = ScanBugsUseCase(self.chain_dir, self.repo, self.graph)
        return scanner.handle_security(query)

    def _handle_trace(self, query: Query) -> dict | None:
        """Handle trace:funcA->funcB queries."""
        target = query.get("trace")
        if not target or "->" not in target:
            return None

        from_func, _, to_func = target.partition("->")
        from_func, to_func = from_func.strip(), to_func.strip()

        graph = self.graph
        from_nodes = [
            (nid, n)
            for nid, n in graph.nodes.items()
            if from_func in n.name and n.type in ("function", "method", "file")
        ]
        to_nodes = [
            (nid, n)
            for nid, n in graph.nodes.items()
            if to_func in n.name and n.type in ("function", "method", "file")
        ]

        if not from_nodes or not to_nodes:
            return {
                "query": f"trace:{target}",
                "trace": f"{from_func} → {to_func}",
                "error": "Nodes not found",
            }

        # BFS to find shortest path
        fnid = from_nodes[0][0]
        tnid = to_nodes[0][0]
        visited = {fnid}
        queue = [(fnid, [fnid])]
        path = None
        while queue:
            cur, p = queue.pop(0)
            if cur == tnid:
                path = p
                break
            for oe in graph.get_outgoing(cur):
                if oe["type"] == "CALLS" and oe["to"] not in visited:
                    visited.add(oe["to"])
                    queue.append((oe["to"], p + [oe["to"]]))

        if not path:
            return {
                "query": f"trace:{target}",
                "trace": f"{from_func} → {to_func}",
                "error": "No path found",
            }

        trace_steps = []
        for nid in path:
            n = graph.get_node(nid)
            if n:
                trace_steps.append(
                    {"name": n.name, "type": n.type, "file": n.path, "line": n.line}
                )
        return {
            "query": f"trace:{target}",
            "trace": f"{from_func} → {to_func}",
            "path": trace_steps,
            "steps": len(trace_steps),
        }

    def _handle_relations(self, query: Query) -> dict | None:
        """Handle relation queries: calls, called_by, imports, imported_by."""
        relation_keys = {"calls", "called_by", "imports", "imported_by", "contains"}
        has_relation = any(f.key in relation_keys for f in query.filters)
        if not has_relation:
            return None

        matched = find_nodes(self.graph, query.filters, query.free_text)
        return {
            "query": query.to_query_text() or "relation_search",
            "total": len(matched),
            "matches": matched[:50],
            "truncated": len(matched) > 50,
        }

    def read(self, file_path: str, verbose: bool = False) -> dict:
        """Read a file through the CPG (structured symbols, not raw text)."""
        graph = self.graph
        file_nodes = [(nid, n) for nid, n in graph.nodes.items() if file_path in n.path]

        if not file_nodes:
            return {"error": f"File '{file_path}' not found in CPG"}

        file_node_id = next((nid for nid, n in file_nodes if n.type == "file"), None)

        # Resolve on disk for verbose mode
        if verbose:
            cwd = os.getcwd()
            for _ in range(10):
                if os.path.isdir(os.path.join(cwd, ".cognitive")):
                    os.path.join(cwd, ".cognitive")
                    break
                parent = os.path.dirname(cwd)
                if parent == cwd:
                    break
                cwd = parent

        symbols = []
        seen_names: set = set()
        for nid, node in file_nodes:
            if node.extra.get("kind") == "external_call":
                continue
            if node.type in ("function", "method", "class"):
                name = node.name
                if name in seen_names:
                    continue
                seen_names.add(name)
                sym = {
                    "name": name,
                    "type": node.type,
                    "line": node.line,
                    "signature": node.signature,
                }
                calls = [
                    graph.get_node(oe["to"]).name
                    for oe in graph.get_outgoing(nid)
                    if oe["type"] == "CALLS" and graph.get_node(oe["to"])
                ]
                if calls:
                    sym["calls"] = calls
                called_by = []
                for ie in graph.get_incoming(nid):
                    src = graph.get_node(ie["from"])
                    if (
                        ie["type"] != "CONTAINS"
                        and ie.get("from") != file_node_id
                        and src
                    ):
                        called_by.append(f"{src.name} ({src.path})")
                if called_by:
                    sym["called_by"] = called_by
                if node.cyclo:
                    sym["cyclo"] = node.cyclo
                symbols.append(sym)

        result = {
            "file": file_path,
            "total_symbols": len(symbols),
            "symbols": symbols,
        }
        if verbose:
            result["note"] = "Use 'walker3 <project>/task/query' for source access"
        return result
