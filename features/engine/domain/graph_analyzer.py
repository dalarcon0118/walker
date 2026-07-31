"""domain/graph_analyzer.py — GraphAnalyzer: algoritmos de teoria de grafos.

Aplica teoria de grafos de flujos de trabajo (van der Aalst / BPMN)
al WorkflowGraph para derivar propiedades utiles:

  - reachability():   desde un nodo, se puede llegar a otro (y distancia)
  - cycles():         detecta ciclos en el grafo (loops del workflow)
  - shortest_path():  camino mas corto entre dos nodos (BFS)
  - distance_to():    distancia de un nodo al objetivo (ej: CLOSE)
  - progress_map():   mapa de progreso del agente (donde estas + ruta)

PURAS: no hacen IO, no persisten. Operan sobre WorkflowGraph.
"""
from __future__ import annotations

from collections import deque
from typing import Any


class GraphAnalyzer:
    """Analisis de grafos sobre el WorkflowGraph (puro)."""

    def __init__(self, graph: Any):
        self.graph = graph
        self._adj = self._build_adjacency()

    def _build_adjacency(self) -> dict[str, list[tuple[str, str]]]:
        """Construye lista de adyacencia: node -> [(gate, target)]."""
        adj: dict[str, list[tuple[str, str]]] = {}
        for source, gates in self.graph.edges_by_source.items():
            adj[source] = [(g.name, g.target_node) for g in gates]
        # Nodos sin salidas tambien entran (sinks)
        for nid in self.graph.nodes:
            adj.setdefault(nid, [])
        return adj

    # ── 1. Reachability ──────────────────────────────────────────────────

    def reachability(self, start: str, target: str, max_hops: int = 10) -> tuple[bool, int, list[str]]:
        """¿Se puede llegar de start a target? Retorna (ok, distancia, path)."""
        if start not in self._adj:
            return False, -1, []
        q: deque[tuple[str, int, list[str]]] = deque([(start, 0, [start])])
        visited = set()
        while q:
            node, dist, path = q.popleft()
            if node == target and dist > 0:
                return True, dist, path
            if dist >= max_hops or node in visited:
                continue
            visited.add(node)
            for _, nxt in self._adj.get(node, []):
                q.append((nxt, dist + 1, path + [nxt]))
        return False, -1, []

    def can_return_to(self, node: str, hub: str = "SPRINT") -> bool:
        """¿Desde node se puede volver al hub (ej: dashboard)?"""
        ok, _, _ = self.reachability(node, hub)
        return ok

    # ── 2. Ciclos ────────────────────────────────────────────────────────

    def cycles(self) -> list[list[str]]:
        """Detecta ciclos en el grafo (DFS con colores)."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in self._adj}
        found: list[list[str]] = []

        def dfs(u: str, stack: list[str]) -> None:
            color[u] = GRAY
            stack.append(u)
            for _, v in self._adj.get(u, []):
                if v not in color:
                    color[v] = WHITE
                if color[v] == GRAY:
                    # Ciclo detectado: desde v hasta u en el stack
                    idx = stack.index(v) if v in stack else 0
                    cycle = stack[idx:] + [v]
                    if cycle not in found:
                        found.append(cycle)
                elif color[v] == WHITE:
                    dfs(v, stack)
            stack.pop()
            color[u] = BLACK

        for n in self._adj:
            if color[n] == WHITE:
                dfs(n, [])
        return found

    # ── 3. Shortest path (BFS) ───────────────────────────────────────────

    def shortest_path(self, start: str, target: str) -> tuple[int, list[str]]:
        """Camino mas corto (menos hops) de start a target. (dist, path)."""
        ok, dist, path = self.reachability(start, target)
        if not ok:
            return -1, []
        return dist, path

    def distance_to(self, node: str, target: str = "SPRINT") -> int:
        """Distancia (hops) de node a target. -1 si inalcanzable."""
        ok, dist, _ = self.reachability(node, target)
        return dist if ok else -1

    # ── 4. Nodos terminales (estados finales) ────────────────────────────

    def terminal_nodes(self) -> list[str]:
        """Nodos sin salidas (sinks) o cuyo ultimo sub-estado no avanza."""
        terminals = []
        for nid in self._adj:
            if not self._adj[nid]:
                terminals.append(nid)
                continue
            node = self.graph.get_node(nid)
            if node and node.sub_states:
                last = node.sub_states[-1]
                if not last.on_success:
                    terminals.append(nid)
        return terminals

    # ── 5. Mapa de progreso (dónde estás + ruta a CLOSE) ─────────────────

    def progress_map(self, current_node: str) -> dict:
        """Construye el mapa de progreso para el status del agente.

        Retorna:
            {
              "position": current_node,
              "can_return_to_hub": bool,
              "distance_to_terminal": int,   # hops al estado final
              "path_to_terminal": [...],      # ruta sugerida
              "cycles_in_graph": int,         # cantidad de loops del grafo
              "terminal": "CLOSE" o el nodo final alcanzable
            }
        """
        terminals = self.terminal_nodes()
        # Elegir el terminal alcanzable mas cercano
        best_dist = -1
        best_term = ""
        best_path: list[str] = []
        for term in terminals:
            dist, path = self.shortest_path(current_node, term)
            if dist >= 0 and (best_dist == -1 or dist < best_dist):
                best_dist = dist
                best_term = term
                best_path = path

        cycles = self.cycles()
        return {
            "position": current_node,
            "can_return_to_hub": self.can_return_to(current_node),
            "distance_to_terminal": best_dist,
            "path_to_terminal": best_path,
            "terminal": best_term,
            "cycles_in_graph": len(cycles),
            "cycle_nodes": sorted({n for c in cycles for n in c})[:8],
        }
