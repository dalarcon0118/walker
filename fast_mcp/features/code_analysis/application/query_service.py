"""application/query_service.py — QueryGraphUseCase.

Entry point for querying the CPG: grep, read, structured access.

Result pattern: los cores puros con @safe devuelven Result (nunca lanzan);
los wrappers deciden con is_successful y hacen fallback explícito con log
(convention-001: nunca except silencioso).
"""

from __future__ import annotations
import logging
import os

from returns.pipeline import is_successful
from returns.result import safe

from fast_mcp.features.code_analysis.domain.graph import Graph
from fast_mcp.features.code_analysis.domain.query import Query
from fast_mcp.features.code_analysis.infrastructure.cognitive_cpg_repo import CognitiveCpgRepository
from fast_mcp.features.code_analysis.infrastructure.filters import find_nodes

logger = logging.getLogger(__name__)


class QueryGraphUseCase:
    """Query the CPG graph: grep nodes, read file structure, traverse relations."""

    def __init__(
        self, chain_dir: str, project_id: str = "", data_dir: str | None = None
    ):
        self.chain_dir = chain_dir
        self._project_id = project_id
        self._data_dir = data_dir
        self._graph: Graph | None = None
        self._repo: CognitiveCpgRepository | None = None

    @property
    def repo(self) -> CognitiveCpgRepository:
        if self._repo is None:
            # W2 (ADR-076): fail-fast sin fallback de rutas (ADR-054 D4).
            # El caller SIEMPRE inyecta data_dir resuelto vía
            # ProjectService.resolve_paths; adivinar <root>/data crea una
            # BD parásita en la raíz del repo (anti-patrón R-A).
            if not self._data_dir:
                raise ValueError(
                    "QueryGraphUseCase.repo: data_dir no inyectado. "
                    "Pase data_dir=paths.data_dir resuelto por "
                    "ProjectService.resolve_paths (ADR-054 D4)."
                )
            self._repo = CognitiveCpgRepository(self._data_dir, self._project_id)
        return self._repo

    @property
    def graph(self) -> Graph:
        if self._graph is None:
            self._graph = self._load_graph()
        return self._graph

    def _load_graph(self) -> Graph:
        """Carga el grafo CPG con fallback explícito.

        Result pattern: el core @safe nunca lanza; ante CPG corrupto
        devuelve un grafo vacio y loggea el error (convention-001).
        """
        result = _load_graph_core(self.repo, self._project_id)
        if is_successful(result):
            return result.value_or(Graph({}, []))
        error = result.failure()
        logger.error(
            "Carga del grafo CPG fallo (%s: %s); se devuelve grafo vacio",
            type(error).__name__, error,
        )
        return Graph({}, [])

    def clear_cache(self) -> None:
        self._graph = None

    def business_layer(self, focus: str = "") -> dict:
        """Retorna la capa de negocio del CPG (funciones puras, flujos, patrones).

        Capa derivada por BusinessAnalyzer durante el build:
          - pure_functions: funciones reutilizables sin side-effects
          - business_flows: cadenas CALLS completas (flujos de negocio)
          - architecture_patterns: TEA, hooks, redux, pure_domain
          - metrics: acoplamiento, funciones huerfanas

        Args:
            focus: filtro de archivo (substring) para limitar resultados.
        """
        result = _business_layer_core(self.repo, self._project_id)
        if is_successful(result):
            layer = result.value_or({})
        else:
            error = result.failure()
            logger.error(
                "Capa de negocio fallo (%s: %s); se devuelve vacia",
                type(error).__name__, error,
            )
            layer = {}
        if not focus:
            # Aplicar cap de chain tambien al leer del CPG almacenado
            return self._cap_flows(layer)
        # Filtrar por archivo
        result = {
            "pure_functions": [p for p in layer.get("pure_functions", [])
                               if focus in p.get("file", "")],
            "business_flows": [f for f in layer.get("business_flows", [])
                               if focus in f.get("file", "")],
            "architecture_patterns": [p for p in layer.get("architecture_patterns", [])
                                      if focus in p.get("file", "")],
            "metrics": layer.get("metrics", {}),
        }
        return self._cap_flows(result)

    @staticmethod
    def _cap_flows(layer: dict, max_chain: int = 40) -> dict:
        """Limita el tamano de las chains de business_flows.

        Evita que flows con cientos de elementos (arboles CALLS gigantes)
        generen ruido en el contexto del agente. El entrypoint + primeros
        pasos son lo util; el detalle completo no aporta.
        """
        flows = layer.get("business_flows") or []
        capped = []
        for f in flows:
            chain = f.get("chain") or []
            if len(chain) > max_chain:
                f = dict(f)
                f["chain"] = chain[:max_chain] + ["..."]
            capped.append(f)
        if capped:
            layer = dict(layer)
            layer["business_flows"] = capped
        return layer

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
        from fast_mcp.features.code_analysis.application.bug_service import ScanBugsUseCase

        scanner = ScanBugsUseCase(self.chain_dir, self.repo, self.graph)
        return scanner.handle_bugs(query)

    def _handle_security(self, query: Query) -> dict | None:
        """Handle security:* queries."""
        from fast_mcp.features.code_analysis.application.bug_service import ScanBugsUseCase

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
            result["note"] = "Use 'walker <project>/search_memories' for source access"
        return result


# ── Cores puros (@safe) ─────────────────────────────────────────

@safe
def _load_graph_core(repo: CognitiveCpgRepository, project_id: str) -> Graph:
    """Core puro de carga del grafo CPG (puede lanzar)."""
    return Graph.from_dict(repo.load(project_id))


@safe
def _business_layer_core(repo: CognitiveCpgRepository, project_id: str) -> dict:
    """Core puro de la capa de negocio (puede lanzar)."""
    data = repo.load(project_id)
    layer = data.get("business_layer", {})
    if not layer:
        # Capa no construida aun: derivarla on-the-fly
        from fast_mcp.features.code_analysis.domain.services.business_analyzer import analyze
        layer = analyze(data.get("nodes", {}), data.get("edges", []))
    return layer
