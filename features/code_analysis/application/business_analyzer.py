"""application/business_analyzer.py — Capa de negocio sobre el CPG.

Convierte el CPG (grafo crudo) en DATOS DE NEGOCIO que el agente puede
consumir para generar codigo SIN alucinar y REUTILIZANDO lo que existe.

Capas que construye:
  1. Pure Functions  — funciones reutilizables sin side-effects (puras)
  2. Business Flows  — cadenas CALLS completas (flujos de negocio)
  3. Architecture    — patrones arquitectonicos detectados (TEA, hooks, etc.)

SRP: Analiza el grafo CPG. No hace IO de codigo fuente.
SSOT: El CPG (nodos + edges) es la entrada; el resultado es derivable.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

# ── Heurísticas de "pureza": imports/símbolos que introducen side-effects ──
_IO_MODULES = re.compile(
    r"(fetch|axios|http|https|ws://|wss://|localStorage|sessionStorage|"
    r"indexedDB|firebase|aws-|@react-native-async-storage|fs\b|path\b|"
    r"child_process|net\b|dgram|dns|readline|zlib|stream|os\b|crypto\b|"
    r"console\.log|process\.|window\.|document\.|Date\.now|Math\.random)"
)
_NO_IO_TYPES = {"constant", "interface", "struct", "enum", "type_alias"}


def _is_pure_signature(sig: str) -> bool:
    """Una funcion es pura si su firma no referencia IO externo."""
    if not sig:
        return False
    return not _IO_MODULES.search(sig)


class BusinessAnalyzer:
    """Analiza el grafo CPG y produce la capa de negocio."""

    def __init__(self, nodes: dict, edges: list):
        self.nodes = nodes
        self.edges = edges
        self._by_path: dict[str, list[str]] = defaultdict(list)
        self._fn_nodes: dict[str, dict] = {}   # name -> node
        self._fn_by_path: dict[str, list[str]] = defaultdict(list)
        self._calls: dict[str, set[str]] = defaultdict(set)  # caller -> callees

        self._index()

    # ── Indexado ──────────────────────────────────────────────────────────

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
                caller = self.nodes.get(e.get("from", ""), {}).get("name", "")
                callee = self.nodes.get(e.get("to", ""), {}).get("name", "")
                if caller and callee:
                    self._calls[caller].add(callee)

    # ── Capa 1: Pure Functions ────────────────────────────────────────────

    def pure_functions(self, min_len: int = 0) -> list[dict]:
        """Funciones reutilizables SIN side-effects.

        Criterios:
          - signature no referencia IO externo
          - no es import
          - (opcional) longitud minima para excluir trivialidades
        """
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

    # ── Capa 2: Business Flows ────────────────────────────────────────────

    def business_flows(self, max_depth: int = 5) -> list[dict]:
        """Cadenas CALLS completas = flujos de negocio.

        Para cada funcion "raiz" (entrypoint, no llamada por nadie o
        exportada), camina el grafo de llamadas y produce la cadena.
        """
        # Funciones que son llamadas por alguien (no raiz)
        called_by: dict[str, int] = defaultdict(int)
        for caller, callees in self._calls.items():
            for c in callees:
                called_by[c] += 1

        roots = [n for n in self._fn_nodes if called_by.get(n, 0) == 0]
        flows = []
        for root in roots:
            chain = self._walk_flow(root, max_depth, set())
            if len(chain) >= 2:  # Solo flujos con al menos una llamada
                flows.append({
                    "entrypoint": root,
                    "file": self._fn_nodes[root].get("path", ""),
                    "chain": chain,
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

    # ── Capa 3: Architecture patterns ─────────────────────────────────────

    def architecture_patterns(self) -> list[dict]:
        """Detecta patrones arquitectonicos por archivo.

        Patrones reconocidos:
          - TEA module: exporta msg/init/update/view (The Elm Architecture)
          - Hook: nombre empieza con 'use' y tipo function
          - Pure domain: solo funciones puras + tipos (sin IO)
          - Redux slice: action/reducer
          - Service: archivo con funciones que llaman a repos/use-cases
        """
        patterns = []
        for path, nids in self._by_path.items():
            nodes = [self.nodes[nid] for nid in nids if isinstance(self.nodes[nid], dict)]
            names = {n.get("name", "") for n in nodes}
            sigs = " ".join(n.get("signature", "") for n in nodes if n.get("signature"))
            types = {n.get("type", "") for n in nodes}

            # TEA: msg/init/update/view
            if any("Msg" in nm or nm.endswith("Msg") for nm in names) and \
               any(nm in ("init", "update", "view") for nm in names):
                patterns.append({"pattern": "tea_module", "file": path,
                                "symbols": sorted(n for n in names if n)})
            # Hook
            if any(nm.startswith("use") for nm in names):
                patterns.append({"pattern": "hook", "file": path,
                                "symbols": sorted(n for n in names if n.startswith("use"))})
            # Redux slice
            if any("slice" in nm.lower() for nm in names) or \
               ("reducer" in names and "action" in names):
                patterns.append({"pattern": "redux_slice", "file": path,
                                "symbols": sorted(n for n in names if n)})
            # Pure domain (sin IO, solo constantes/funciones puras/tipos)
            if types and types.issubset(_NO_IO_TYPES | {"function"}) and sigs and \
               not _IO_MODULES.search(sigs):
                patterns.append({"pattern": "pure_domain", "file": path,
                                "symbols": sorted(n for n in names if n)})
        return patterns

    # ── Métricas limpias ──────────────────────────────────────────────────

    def metrics(self) -> dict:
        """Metricas de diseño derivadas del grafo."""
        total_fn = len(self._fn_nodes)
        called = {f for fs in self._calls.values() for f in fs}
        orphans = [n for n in self._fn_nodes if n not in called]
        # Acoplamiento: funciones que llaman a muchas otras
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
