#!/usr/bin/env python3
"""DeltaGraph — capa de grafo sobre datos del sprint delta.

Construye un networkx.DiGraph desde plan.json + sprint-state.json,
haciendo explícitas todas las relaciones implícitas entre tasks,
predicates, archivos, símbolos, riesgos y eventos.

Uso:
    from graph_utils import DeltaGraph

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

    Construye un networkx.DiGraph desde plan.json + sprint-state.json,
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
        state_path = os.path.join(self.chain_dir, "sprint-state.json")

        # Cargar CPG y CKG para unified_context
        project_dir = os.path.dirname(os.path.dirname(self.chain_dir))
        cpg_path = os.path.join(project_dir, "cpg.json")
        ckg_path = os.path.join(project_dir, "ckg.json")

        if os.path.exists(plan_path):
            with open(plan_path) as f:
                self._plan = json.load(f)

        # Cargar sprint-state.json (reemplaza sprint-state.json)
        if os.path.exists(state_path):
            with open(state_path) as f:
                self._progress = json.load(f)
        else:
            self._progress = {}

        # Cargar CPG y CKG para consultas federadas
        self._cpg: dict = {}
        self._ckg: dict = {}
        if os.path.exists(cpg_path):
            with open(cpg_path) as f:
                self._cpg = json.load(f)
        if os.path.exists(ckg_path):
            with open(ckg_path) as f:
                self._ckg = json.load(f)

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

        # ── Trail events (desde sprint-state.json) ──
        trail = []
        if "audit" in self._progress:
            trail = self._progress["audit"].get("trail", [])
        elif "trail" in self._progress:
            trail = self._progress.get("trail", [])
        for ei, evt in enumerate(trail):
            evt_at = evt.get("at") or evt.get("timestamp", str(ei))
            enode = _node_id(NODE_EVENT, f"{ei}:{str(evt_at)[:19]}")
            self.graph.add_node(
                enode,
                type=NODE_EVENT,
                label=evt.get("event", "")[:60],
                distance=evt.get("distance", 0),
            )
            evt_text = evt.get("event", "")
            for t in tasks:
                tid = t.get("task_id", "")
                if tid and tid in evt_text:
                    tnode = _node_id(NODE_TASK, tid)
                    self.graph.add_edge(tnode, enode, type=EDGE_HAS_EVENT)

        # ── CPG↔CKG bridge edges ──
        # Conecta nodos del CPG (cpg.json) con nodos del CKG (ckg.json)
        # cuando comparten el mismo path de archivo
        cpg_nodes = self._cpg.get("nodes", {})
        ckg_nodes = self._ckg.get("nodes", {})
        cpg_bridge = self._cpg.get("cpg_bridge", {}).get("edges", [])

        for bridge_edge in cpg_bridge:
            from_id = bridge_edge.get("from", "")
            to_id = bridge_edge.get("to", "")
            etype = bridge_edge.get("type", "RELATED_TO")
            if from_id and to_id:
                self.graph.add_edge(from_id, to_id, type=etype)

        # Unir nodos CPG y CKG que referencian el mismo archivo
        for nid, node in cpg_nodes.items():
            file_path = node.get("path", "")
            if not file_path:
                continue
            for ckid, cknode in ckg_nodes.items():
                ck_scope = cknode.get("scope", [])
                if any(file_path.startswith(s) for s in ck_scope):
                    self.graph.add_edge(ckid, nid, type="IMPLEMENTS")

    # ── Unified Context ─────────────────────────────────────────

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
        # SSOT: progress.current.task_states
        real_statuses = (
            self._progress.get("progress", {}).get("current", {}).get("task_states", {})
        )
        if real_statuses:
            status = real_statuses.get(tid, {})
            if isinstance(status, dict):
                return status.get("status", "unknown")
            return str(status) if status else "unknown"

        # Fallback: top-level tasks (dict or list)
        ts = self._progress.get("tasks", {})
        if isinstance(ts, dict) and tid in ts:
            return (
                ts[tid].get("status", "unknown")
                if isinstance(ts[tid], dict)
                else str(ts[tid])
            )
        if isinstance(ts, list):
            return "pending" if tid in ts else "unknown"
        return "unknown"

    # ═══════════════════════════════════════════════════════════════
    # TASK-005b: unified_context — contexto federado CPG + CKG
    # ═══════════════════════════════════════════════════════════════

    def unified_context(self, task_id: str | None = None) -> str:
        """Retorna contexto federado (sprint + CPG + CKG) para el LLM.

        Si se especifica task_id, filtra por archivos de esa tarea.
        Incluye advertencia si el CPG está stale.
        """
        parts: list[str] = []
        parts.append("=" * 60)
        parts.append("📋 CONTEXTO UNIFICADO (DeltaGraph + CPG + CKG)")
        parts.append("=" * 60)

        # 1. Sprint state
        parts.append(f"\n📊 Sprint: {self._chain_name()}")
        pl = self._plan.get("plan", self._plan)
        goal = pl.get("goal", {}).get("description", "")
        if goal:
            parts.append(f"  Goal: {goal[:120]}")

        # 2. Task info (if specified)
        if task_id:
            parts.append(f"\n🎯 Tarea: {task_id}")
            for t in pl.get("tasks", []):
                if t.get("task_id") == task_id:
                    parts.append(f"  Objective: {t.get('objective', '')[:120]}")
                    parts.append(f"  Status: {self._task_status(task_id)}")
                    files = t.get("impacted_artifacts", {}).get("files", [])
                    if files:
                        parts.append(f"  Files: {', '.join(files)}")
                    break

        # 3. CPG stats
        cpg_nodes = self._cpg.get("nodes", {})
        cpg_edges = self._cpg.get("edges", [])
        cpg_updated = self._cpg.get("updated_at", self._cpg.get("generated_at", ""))
        parts.append(f"\n🔷 CPG: {len(cpg_nodes)} nodos, {len(cpg_edges)} edges")
        parts.append(f"  Generado: {cpg_updated[:19]}")

        # Stale CPG warning
        if cpg_updated:
            from datetime import datetime, timezone

            try:
                updated = datetime.fromisoformat(cpg_updated)
                age = (datetime.now(timezone.utc) - updated).total_seconds()
                if age > 3600:  # 1 hora
                    parts.append(
                        f"  ⚠️  CPG stale ({(age / 60):.0f} min). Usa --refresh-cpg"
                    )
            except (ValueError, TypeError):
                pass

        # 4. CKG stats
        ckg_nodes = self._ckg.get("nodes", {})
        ckg_edges = self._ckg.get("edges", [])
        parts.append(f"\n💡 CKG: {len(ckg_nodes)} nodos, {len(ckg_edges)} edges")

        # 5. Bridge edges relevantes a la tarea
        if task_id:
            task_files = []
            for t in pl.get("tasks", []):
                if t.get("task_id") == task_id:
                    task_files = t.get("impacted_artifacts", {}).get("files", [])
                    break
            if task_files:
                bridge_count = 0
                for edge in self._cpg.get("cpg_bridge", {}).get("edges", []):
                    if any(f in str(edge) for f in task_files):
                        bridge_count += 1
                if bridge_count:
                    parts.append(
                        f"\n🔗 Bridge edges CPG↔CKG: {bridge_count} relevantes"
                    )

        parts.append("\n" + "=" * 60)
        return "\n".join(parts)
