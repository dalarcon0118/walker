"""CKG Query Service — búsqueda en el Code Knowledge Graph con índices cacheados.

Uso:
    from features.cognitive_knowledge.application.query import CkgQueryService
    svc = CkgQueryService(chain_dir)
    svc.query("type:decision scope:workflow")
    svc.neighbors("OrchestrationService")
    svc.briefing("TASK-013")
"""

from __future__ import annotations

import json
import os
from typing import Any


class CkgQueryService:
    """Servicio de consultas al CKG con cache de índices.

    Los índices by_type y by_scope se construyen una vez y se cachean
    para evitar O(n) en cada query.
    """

    _cache: dict[str, Any] = {}

    def __init__(self, chain_dir: str) -> None:
        self.chain_dir = chain_dir
        self._data: dict | None = None
        self._nodes: list[dict] = []
        self._edges: list[dict] = []
        self._by_type: dict[str, list[dict]] = {}
        self._by_scope: dict[str, list[dict]] = {}

    # ── Load / Index ────────────────────────────────────────────

    def _load(self) -> None:
        if self._data is not None:
            return
        from features.project import ProjectService
        paths = ProjectService.resolve_paths(self.chain_dir)
        project_dir = paths.project_root
        # Buscar ckg.json.gz (canónico) o ckg.json (legacy)
        ckg_candidates = [
            paths.ckg_path,
            os.path.join(project_dir, "data", "ckg.json"),
            os.path.join(project_dir, "ckg.json"),
        ]
        ckg_path = next((p for p in ckg_candidates if os.path.exists(p)), None) or ""
        cache_key = os.path.abspath(ckg_path) if ckg_path else ""

        if cache_key and cache_key in self._cache:
            cached = self._cache[cache_key]
            self._data = cached["data"]
            self._nodes = cached["nodes"]
            self._edges = cached["edges"]
            self._by_type = cached["by_type"]
            self._by_scope = cached["by_scope"]
            return

        data = {"nodes": [], "edges": []}
        if os.path.exists(ckg_path):
            try:
                if ckg_path.endswith(".gz"):
                    import gzip as _gz
                    with _gz.open(ckg_path, "rt") as f:
                        raw = json.load(f)
                else:
                    with open(ckg_path) as f:
                        raw = json.load(f)
                # Soporta formato {index, docs} y {nodes, edges}
                if "docs" in raw and isinstance(raw["docs"], list):
                    raw = {"nodes": raw["docs"], "edges": []}
                nodes = raw.get("nodes", [])
                if isinstance(nodes, dict):
                    nodes = list(nodes.values())
                raw["nodes"] = nodes
                data = raw
            except (json.JSONDecodeError, OSError, _gz.BadGzipFile):
                pass

        self._data = data
        self._nodes = data.get("nodes", [])
        self._edges = data.get("edges", [])

        # Build indexes O(n)
        for n in self._nodes:
            ntype = n.get("type", "?")
            self._by_type.setdefault(ntype, []).append(n)
            for scope in n.get("scope", []):
                self._by_scope.setdefault(str(scope), []).append(n)

        if cache_key:
            self._cache[cache_key] = {
                "data": self._data,
                "nodes": self._nodes,
                "edges": self._edges,
                "by_type": self._by_type,
                "by_scope": self._by_scope,
            }

    # ── Query ───────────────────────────────────────────────────

    def query(self, query_str: str) -> dict:
        """Ejecuta una query contra el CKG.

        Formatos:
          type:decision                → filtro por tipo
          scope:TASK-013               → filtro por scope
          label:Orchestration          → búsqueda en label
          type:convention scope:engine → combinación
          OrchestrationService         → texto libre en label/description
        """
        self._load()
        filters, free_text = self._parse_query(query_str)
        matched = self._execute(filters, free_text)
        return {
            "query": query_str,
            "total": len(matched),
            "matches": matched[:30],
            "truncated": len(matched) > 30,
            "indexes": {
                "by_type": len(self._by_type),
                "by_scope": len(self._by_scope),
            },
        }

    def neighbors(self, node_ref: str) -> dict:
        """Retorna vecinos de un nodo por ID o label."""
        self._load()
        node_id = self._resolve_node(node_ref)
        if not node_id:
            return {"query": node_ref, "neighbors": 0, "relations": []}

        related = []
        for e in self._edges:
            if e.get("from") == node_id:
                related.append(
                    {
                        "relation": e.get("type", "?"),
                        "to": e.get("to", ""),
                        "direction": "out",
                    }
                )
            if e.get("to") == node_id:
                related.append(
                    {
                        "relation": e.get("type", "?"),
                        "from": e.get("from", ""),
                        "direction": "in",
                    }
                )
        return {
            "query": node_ref,
            "node": node_id,
            "neighbors": len(related),
            "relations": related[:20],
            "truncated": len(related) > 20,
        }

    def briefing(
        self, task_id: str | None = None, module_scope: str | None = None
    ) -> dict:
        """Briefing pre-implementación: contexto completo del CKG.

        Busca nodos relacionados con la tarea por:
        1. metadata.task_id (campo exacto)
        2. scope (si coincide con un scope conocido)
        3. label/description (texto libre)
        """
        self._load()

        # 1. Buscar por metadata.task_id
        by_task = []
        for n in self._nodes:
            meta = n.get("metadata", {})
            if isinstance(meta, dict) and meta.get("task_id") == task_id:
                by_task.append(n)

        # 2. Buscar por scope
        scope = module_scope or ""
        if task_id and not scope:
            # Intentar derivar scope del sprint actual
            scope_parts = [s for s in self._by_scope if task_id and task_id in str(s)]
            scope = scope_parts[0] if scope_parts else ""

        # 3. Combinar resultados
        matched_nodes = by_task[:]
        if scope:
            for n in self._by_scope.get(scope, []):
                if n not in matched_nodes:
                    matched_nodes.append(n)

        def _filter(ntype: str) -> list[dict]:
            return [
                {
                    "id": n.get("id", ""),
                    "label": n.get("label", "")[:100],
                    "description": (n.get("description", "") or "")[:150],
                    "scope": n.get("scope", []),
                }
                for n in matched_nodes
                if n.get("type") == ntype
            ]

        scope_modules = [
            n.get("label", "") for n in matched_nodes if n.get("type") == "module"
        ]

        return {
            "briefing_for": task_id or module_scope or "general",
            "conventions": {
                "total": len(
                    [n for n in matched_nodes if n.get("type") == "convention"]
                ),
                "items": [m["label"] for m in _filter("convention")[:10]],
            },
            "decisions": {
                "total": len([n for n in matched_nodes if n.get("type") == "decision"]),
                "items": [m["label"] for m in _filter("decision")[:10]],
            },
            "warnings": {
                "total": len([n for n in matched_nodes if n.get("type") == "warning"]),
                "items": [m["label"] for m in _filter("warning")[:5]],
            },
            "outside_scope": {
                "total": len(
                    [n for n in self._nodes if n.get("type") == "outside_scope"]
                ),
                "items": [
                    m["label"]
                    for m in self._execute([("type", "outside_scope")], None)[:5]
                ],
            },
            "modules": scope_modules,
            "total_nodes_found": len(matched_nodes),
        }

    # ── Internals ───────────────────────────────────────────────

    def _parse_query(self, query: str) -> tuple[list[tuple[str, str]], str | None]:
        filters: list[tuple[str, str]] = []
        free_text: str | None = None
        for part in query.strip().split():
            if ":" in part:
                key, _, val = part.partition(":")
                if key and val:
                    filters.append((key, val))
            else:
                free_text = part if free_text is None else free_text + " " + part
        return filters, free_text

    def _execute(
        self, filters: list[tuple[str, str]], free_text: str | None
    ) -> list[dict]:
        candidates = self._nodes

        # Use type/scope indexes for O(1) filtering
        for k, v in filters:
            if k == "type" and v in self._by_type:
                candidates = self._by_type[v]
                break
            if k == "scope" and v in self._by_scope:
                candidates = self._by_scope[v]
                break

        matched = []
        for node in candidates:
            if not self._match(node, filters):
                continue
            if free_text:
                label = node.get("label", "")
                desc = node.get("description", "")
                if free_text not in label and free_text not in desc:
                    continue
            matched.append(
                {
                    "id": node.get("id", ""),
                    "type": node.get("type", "?"),
                    "label": node.get("label", "")[:80],
                    "description": (node.get("description", "") or "")[:120],
                    "scope": node.get("scope", [])[:3],
                }
            )
        return matched

    def _match(self, node: dict, filters: list[tuple[str, str]]) -> bool:
        for key, val in filters:
            if not key:
                continue
            if key == "type":
                if node.get("type") != val:
                    return False
            elif key == "label":
                if val not in node.get("label", ""):
                    return False
            elif key == "scope":
                if val not in [str(s) for s in node.get("scope", [])]:
                    return False
            elif key == "id":
                if val not in node.get("id", ""):
                    return False
            elif val not in str(node.get(key, "")):
                return False
        return True

    def _resolve_node(self, ref: str) -> str | None:
        """Resuelve un nombre a ID de nodo."""
        for n in self._nodes:
            if ref == n.get("id", ""):
                return ref
            if ref in n.get("label", ""):
                return n.get("id", "") or None
        return None
