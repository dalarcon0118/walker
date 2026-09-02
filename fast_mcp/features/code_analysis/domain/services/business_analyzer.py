"""domain/services/business_analyzer.py — Lógica pura de análisis de negocio del CPG.

SRP: transforma el grafo CPG crudo (nodes + edges) en datos de negocio
que el agente puede consumir para generar código sin alucinar y reutilizando
lo que existe.

Capa 1: Pure Functions — funciones reutilizables sin side-effects
Capa 2: Business Flows — cadenas CALLS completas (flujos de negocio)
Capa 3: Architecture Patterns — patrones arquitectónicos detectados

No hace IO de código fuente ni accede a CognitiveGraph directamente.
Recibe nodes/edges como parámetros (inyección de dependencias).
"""
from __future__ import annotations

import re
from collections import defaultdict


_IO_MODULES = re.compile(
    r"(fetch|axios|http|https|ws://|wss://|localStorage|sessionStorage|"
    r"indexedDB|firebase|aws-|@react-native-async-storage|fs\b|path\b|"
    r"child_process|net\b|dgram|dns|readline|zlib|stream|os\b|crypto\b|"
    r"console\.log|process\.|window\.|document\.|Date\.now|Math\.random)"
)
_NO_IO_TYPES = {"constant", "interface", "struct", "enum", "type_alias"}


def _is_pure_signature(sig: str) -> bool:
    if not sig:
        return False
    return not _IO_MODULES.search(sig)


class BusinessAnalyzer:
    """Analiza el grafo CPG y produce la capa de negocio."""

    def __init__(self, nodes: dict[str, dict], edges: list[dict]):
        self.nodes = nodes
        self.edges = edges
        self._by_path: dict[str, list[str]] = defaultdict(list)
        self._fn_nodes: dict[str, dict] = {}
        self._fn_by_path: dict[str, list[str]] = defaultdict(list)
        self._calls: dict[str, set[str]] = defaultdict(set)
        self._index()

    def _index(self) -> None:
        for nid, n in self.nodes.items():
            if not isinstance(n, dict):
                continue
            path = n.get("path", "")
            self._by_path[path].append(nid)
            if n.get("type") in ("function", "method") and not n.get("is_import"):
                name = n.get("name", "")
                self._fn_nodes[name] = n
                self._fn_by_path[path].append(nid)

        for e in self.edges:
            if e.get("type") == "CALLS":
                caller_node = self.nodes.get(e.get("from", ""), {})
                callee_node = self.nodes.get(e.get("to", ""), {})
                if not isinstance(caller_node, dict) or not isinstance(callee_node, dict):
                    continue
                if caller_node.get("type") not in ("function", "method"):
                    continue
                if callee_node.get("type") not in ("function", "method"):
                    continue
                if caller_node.get("is_import") or callee_node.get("is_import"):
                    continue
                caller = caller_node.get("name", "")
                callee = callee_node.get("name", "")
                if caller and callee:
                    self._calls[caller].add(callee)

    def pure_functions(self, min_len: int = 0) -> list[dict]:
        result = []
        for nid, n in self.nodes.items():
            if not isinstance(n, dict):
                continue
            if n.get("type") not in ("function", "constant", "method"):
                continue
            if n.get("is_import"):
                continue
            sig = n.get("signature", "")
            if not sig:
                continue
            if not _is_pure_signature(sig):
                continue
            loc = n.get("loc") or 0
            if min_len and loc < min_len:
                continue
            result.append({
                "id": nid,
                "name": n.get("name", ""),
                "type": n.get("type", ""),
                "file": n.get("path", ""),
                "line": n.get("line", 0),
                "signature": sig,
                "loc": loc,
            })
        return result

    def business_flows(self, max_depth: int = 5, max_chain: int = 40) -> list[dict]:
        called_by: dict[str, int] = defaultdict(int)
        for caller, callees in self._calls.items():
            for c in callees:
                called_by[c] += 1

        roots = [n for n in self._fn_nodes if called_by.get(n, 0) == 0]
        flows = []
        for root in roots:
            chain = self._walk_flow(root, max_depth, set())
            if len(chain) >= 2:
                truncated = chain[:max_chain]
                if len(chain) > max_chain:
                    truncated.append("...")
                flows.append({
                    "entrypoint": root,
                    "file": self._fn_nodes[root].get("path", ""),
                    "chain": truncated,
                })
        return flows

    def _walk_flow(self, fn: str, depth: int, seen: set) -> list[str]:
        if depth <= 0 or fn in seen:
            return [fn]
        seen.add(fn)
        chain = [fn]
        for callee in sorted(self._calls.get(fn, [])):
            if callee in self._fn_nodes and callee not in seen:
                chain.extend(self._walk_flow(callee, depth - 1, seen))
        return chain

    def architecture_patterns(self) -> list[dict]:
        patterns = []
        for path, nids in self._by_path.items():
            nodes = [self.nodes[nid] for nid in nids if isinstance(self.nodes[nid], dict)]
            names = {n.get("name", "") for n in nodes}
            sigs = " ".join(n.get("signature", "") for n in nodes if n.get("signature"))
            types = {n.get("type", "") for n in nodes}

            if any("Msg" in nm or nm.endswith("Msg") for nm in names) and any(
                nm in ("init", "update", "view") for nm in names
            ):
                patterns.append({
                    "pattern": "tea_module",
                    "file": path,
                    "symbols": sorted(n for n in names if n),
                })
            if any(nm.startswith("use") for nm in names):
                patterns.append({
                    "pattern": "hook",
                    "file": path,
                    "symbols": sorted(n for n in names if n.startswith("use")),
                })
            if any("slice" in nm.lower() for nm in names) or (
                "reducer" in names and "action" in names
            ):
                patterns.append({
                    "pattern": "redux_slice",
                    "file": path,
                    "symbols": sorted(n for n in names if n),
                })
            if (
                types
                and types.issubset(_NO_IO_TYPES | {"function"})
                and sigs
                and not _IO_MODULES.search(sigs)
            ):
                patterns.append({
                    "pattern": "pure_domain",
                    "file": path,
                    "symbols": sorted(n for n in names if n),
                })
        return patterns

    def metrics(self) -> dict:
        total_fn = len(self._fn_nodes)
        called = {f for fs in self._calls.values() for f in fs}
        orphans = [n for n in self._fn_nodes if n not in called]
        high_fanout = sorted(
            ((n, len(cs)) for n, cs in self._calls.items()),
            key=lambda x: -x[1],
        )[:10]
        return {
            "total_functions": total_fn,
            "orphan_functions": len(orphans),
            "orphan_names": orphans[:10],
            "high_fanout": [{"function": n, "calls": c} for n, c in high_fanout],
            "flows_detected": len(self.business_flows()),
            "pure_functions": len(self.pure_functions()),
        }


def analyze(nodes: dict, edges: list) -> dict:
    """Entry point: construye la capa de negocio completa."""
    ba = BusinessAnalyzer(nodes, edges)
    return {
        "pure_functions": ba.pure_functions(),
        "business_flows": ba.business_flows(),
        "architecture_patterns": ba.architecture_patterns(),
        "metrics": ba.metrics(),
    }
