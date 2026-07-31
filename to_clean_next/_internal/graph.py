#!/usr/bin/env python3
"""DeltaGraph — capa de grafo sobre datos del sprint delta.

Construye un networkx.DiGraph desde plan.json + progress.json,
haciendo explícitas todas las relaciones implícitas entre tasks,
predicates, archivos, símbolos, riesgos y eventos.

Uso:
    from _internal.graph import DeltaGraph

    g = DeltaGraph(chain_dir)
    print(g.neighbors("TASK-005"))
    print(g.path("TASK-005", "spring.py"))
    print(g.critical_path())
    g.export_html("graph.html")
"""

from __future__ import annotations

import json
import os
import re

import networkx as nx

# ── Node prefix conventions ─────────────────────────────────────
NODE_TASK = "task"
NODE_PRED = "pred"
NODE_FILE = "file"
NODE_SYMBOL = "symbol"
NODE_RISK = "risk"
NODE_EVENT = "event"
NODE_SPRINT = "sprint"

# ── Edge type conventions ───────────────────────────────────────
EDGE_DEPENDS_ON = "depends_on"
EDGE_HAS_PREDICATE = "has_predicate"
EDGE_VERIFIES_SYMBOL = "verifies_symbol"
EDGE_IMPACTS_FILE = "impacts_file"
EDGE_HAS_RISK = "has_risk"
EDGE_HAS_EVENT = "has_event"
EDGE_HAS_TASK = "has_task"


def _node_id(prefix: str, key: str) -> str:
    return f"{prefix}:{key}"


def _short_label(node_id: str) -> str:
    """Extrae etiqueta legible del node_id."""
    parts = node_id.split(":", 1)
    if len(parts) > 1:
        return parts[1][:60]
    return node_id[:60]


def _extract_symbols(text: str) -> list[str]:
    """Extrae nombres de símbolos (funciones, clases, archivos) de un texto."""
    symbols = set()
    # Funciones: cmd_create, verify_predicate, etc
    for m in re.finditer(r"\b([a-z_][a-z0-9_]*)\s*\(\)", text):
        symbols.add(m.group(1))
    # Clases: DeltaGraph, etc
    for m in re.finditer(r"\b([A-Z][a-zA-Z0-9_]+)\b", text):
        symbols.add(m.group(1))
    # Archivos: archivo.py, archivo.json
    for m in re.finditer(r"\b([\w/.-]+\.(?:py|json|md|txt))\b", text):
        symbols.add(m.group(1))
    return list(symbols)[:10]  # límite práctico


class DeltaGraph:
    """Grafo dirigido de relaciones en datos delta.

    Construye un networkx.DiGraph desde plan.json + progress.json,
    con nodos tipados y edges etiquetados. Los datos fuente no se modifican.
    """

    def __init__(self, chain_dir: str):
        self.chain_dir = os.path.abspath(chain_dir)
        self.graph = nx.DiGraph()
        self._plan: dict = {}
        self._progress: dict = {}
        self._build()

    # ── Build ──────────────────────────────────────────────────

    def _load(self) -> None:
        plan_path = os.path.join(self.chain_dir, "plan.json")
        prog_path = os.path.join(self.chain_dir, "progress.json")

        if os.path.exists(plan_path):
            with open(plan_path) as f:
                self._plan = json.load(f)
        if os.path.exists(prog_path):
            with open(prog_path) as f:
                self._progress = json.load(f)

    def _build(self) -> None:
        self._load()

        pl = self._plan.get("plan", self._plan)
        tasks = pl.get("tasks", [])
        sprint_id = self._chain_name()

        # ── Sprint root node ──
        sid = _node_id(NODE_SPRINT, sprint_id)
        self.graph.add_node(
            sid,
            type=NODE_SPRINT,
            label=sprint_id,
            project_id=pl.get("project", {}).get("project_id", "?"),
        )

        # ── Tasks ──
        for t in tasks:
            tid = t.get("task_id", "?")
            node = _node_id(NODE_TASK, tid)
            status = self._task_status(tid)
            self.graph.add_node(
                node,
                type=NODE_TASK,
                label=tid,
                status=status,
                task_type=t.get("type", "?"),
                objective=t.get("objective", "")[:80],
            )
            self.graph.add_edge(sid, node, type=EDGE_HAS_TASK)

            # depends_on
            for dep_id in t.get("depends_on", []):
                dep_node = _node_id(NODE_TASK, dep_id)
                self.graph.add_node(
                    dep_node,
                    type=NODE_TASK,
                    label=dep_id,
                    status=self._task_status(dep_id),
                )
                self.graph.add_edge(dep_node, node, type=EDGE_DEPENDS_ON)

            # validation predicates
            for pi, p in enumerate(t.get("validation", {}).get("predicates", [])):
                pred_key = f"{tid}:{pi}"
                pnode = _node_id(NODE_PRED, pred_key)
                ptype = p.get("type", "string") if isinstance(p, dict) else "string"
                ptext = str(p)[:80]
                self.graph.add_node(pnode, type=NODE_PRED, label=ptext, pred_type=ptype)
                self.graph.add_edge(node, pnode, type=EDGE_HAS_PREDICATE)

                # Extract symbols from predicate
                raw = (
                    p.get("command", p.get("symbol", str(p)))
                    if isinstance(p, dict)
                    else str(p)
                )
                for sym in _extract_symbols(raw):
                    snode = _node_id(NODE_SYMBOL, sym)
                    self.graph.add_node(snode, type=NODE_SYMBOL, label=sym)
                    self.graph.add_edge(pnode, snode, type=EDGE_VERIFIES_SYMBOL)

                # Link to file if predicate has file hint
                fhint = p.get("file", "") if isinstance(p, dict) else ""
                if fhint:
                    fnode = _node_id(NODE_FILE, fhint)
                    self.graph.add_node(fnode, type=NODE_FILE, label=fhint)
                    self.graph.add_edge(pnode, fnode, type=EDGE_IMPACTS_FILE)

            # impacted_artifacts.files
            for fp in t.get("impacted_artifacts", {}).get("files", []):
                fnode = _node_id(NODE_FILE, fp)
                self.graph.add_node(fnode, type=NODE_FILE, label=fp)
                self.graph.add_edge(node, fnode, type=EDGE_IMPACTS_FILE)

            # risks
            for rk, rv in t.get("risks", {}).items():
                rnode = _node_id(NODE_RISK, f"{tid}:{rk}")
                self.graph.add_node(rnode, type=NODE_RISK, label=f"{rk}:{rv}", value=rv)
                self.graph.add_edge(node, rnode, type=EDGE_HAS_RISK)

        # ── Trail events ──
        pr = self._progress.get("progress", self._progress)
        for ei, evt in enumerate(pr.get("trail", [])):
            evt_at = evt.get("at", str(ei))
            enode = _node_id(NODE_EVENT, f"{ei}:{evt_at[:19]}")
            self.graph.add_node(
                enode,
                type=NODE_EVENT,
                label=evt.get("event", "")[:60],
                distance=evt.get("distance", 0),
            )
            # Link to task if event mentions a task_id
            evt_text = evt.get("event", "")
            for t in tasks:
                tid = t.get("task_id", "")
                if tid and tid in evt_text:
                    tnode = _node_id(NODE_TASK, tid)
                    self.graph.add_edge(tnode, enode, type=EDGE_HAS_EVENT)

    # ── Queries ────────────────────────────────────────────────

    def neighbors(self, node_id: str, edge_type: str | None = None) -> list[dict]:
        """Retorna nodos adyacentes con info del edge."""
        full_node = self._resolve_node(node_id, NODE_TASK)
        if full_node is None:
            return []

        results = []
        for _, neighbor, data in self.graph.out_edges(full_node, data=True):
            if edge_type and data.get("type") != edge_type:
                continue
            neighbor_data = self.graph.nodes.get(neighbor, {})
            results.append(
                {
                    "node": neighbor,
                    "label": neighbor_data.get("label", _short_label(neighbor)),
                    "type": neighbor_data.get("type", "?"),
                    "edge": data.get("type", "?"),
                    "status": neighbor_data.get("status", ""),
                }
            )

        # Also show incoming edges
        for src, _, data in self.graph.in_edges(full_node, data=True):
            if edge_type and data.get("type") != edge_type:
                continue
            src_data = self.graph.nodes.get(src, {})
            results.append(
                {
                    "node": src,
                    "label": src_data.get("label", _short_label(src)),
                    "type": src_data.get("type", "?"),
                    "edge": data.get("type", "?") + " (incoming)",
                    "status": src_data.get("status", ""),
                }
            )

        return results

    def path(self, from_id: str, to_id: str) -> list[dict] | None:
        """Camino más corto entre dos nodos."""
        full_from = self._resolve_node(from_id, NODE_TASK)
        full_to = self._resolve_node(to_id, NODE_FILE)

        if full_from is None or full_to is None:
            return None

        try:
            path_nodes = nx.shortest_path(self.graph, full_from, full_to)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

        result = []
        for i, n in enumerate(path_nodes):
            ndata = self.graph.nodes.get(n, {})
            step = {
                "node": n,
                "label": ndata.get("label", _short_label(n)),
                "type": ndata.get("type", "?"),
            }
            if i < len(path_nodes) - 1:
                edge_data = self.graph.get_edge_data(n, path_nodes[i + 1])
                if edge_data:
                    step["next_edge"] = edge_data.get("type", "?")
            result.append(step)
        return result

    def critical_path(self) -> list[str]:
        """Camino crítico: longest path en DAG de depends_on."""
        dep_graph = nx.DiGraph()
        for u, v, data in self.graph.edges(data=True):
            if data.get("type") == EDGE_DEPENDS_ON:
                dep_graph.add_edge(u, v)

        if dep_graph.number_of_nodes() == 0:
            return []

        try:
            path = nx.dag_longest_path(dep_graph)
        except nx.NetworkXError:
            return []
        return [_short_label(n) for n in path]

    def subgraph(
        self, node_types: list[str] | None = None, status: str | None = None
    ) -> list[dict]:
        """Retorna nodos filtrados por tipo y/o status."""
        results = []
        for n, data in self.graph.nodes(data=True):
            ntype = data.get("type", "?")
            if node_types and ntype not in node_types:
                continue
            if status and data.get("status") != status:
                continue
            results.append(
                {
                    "node": n,
                    "label": data.get("label", _short_label(n)),
                    "type": ntype,
                    "status": data.get("status", ""),
                    "degree": self.graph.degree(n),
                }
            )
        return results

    def export_data(self) -> dict:
        """Exporta el grafo como dict serializable."""
        nodes = []
        for n, data in self.graph.nodes(data=True):
            d = dict(data)
            d["id"] = n
            nodes.append(d)
        edges = []
        for u, v, data in self.graph.edges(data=True):
            edges.append({"source": u, "target": v, "type": data.get("type", "?")})
        return {
            "nodes": nodes,
            "edges": edges,
            "chain_id": self._chain_name(),
            "stats": {
                "nodes": self.graph.number_of_nodes(),
                "edges": self.graph.number_of_edges(),
            },
        }

    def _resolve_node(self, name: str, preferred_type: str | None = None) -> str | None:
        """Resuelve un nombre a un node_id en el grafo.

        Si ``name`` ya tiene formato ``tipo:valor``, se usa directo.
        Si no, busca coincidencias parciales. ``preferred_type`` da prioridad
        a cierto tipo de nodo cuando hay múltiples candidatos.
        """
        if ":" in name:
            if name in self.graph:
                return name
            # Try with just the prefix:name format
            return name if name in self.graph else None

        full = _node_id(NODE_TASK, name)
        if full in self.graph:
            return full

        # Partial match: buscar nodos que contengan el nombre
        candidates = [n for n in self.graph.nodes if name in n]
        if not candidates:
            return None

        # Score candidates: prefer shorter, prefer preferred_type
        def _score(n: str) -> tuple:
            ntype = n.split(":")[0] if ":" in n else ""
            type_bonus = 0 if ntype == preferred_type else 1
            # Preferir exact match on label
            data = self.graph.nodes.get(n, {})
            label = data.get("label", "")
            label_bonus = 0 if label == name else 1
            return (len(n), type_bonus, label_bonus)

        candidates.sort(key=_score)
        return candidates[0]

    def _chain_name(self) -> str:
        return os.path.basename(self.chain_dir)

    def _task_status(self, tid: str) -> str:
        pr = self._progress.get("progress", self._progress)
        ts = pr.get("current", {}).get("task_states", {})
        return ts.get(tid, "unknown")
