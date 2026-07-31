"""CognitiveGraph — Capa de infraestructura tipo ODM para memorias del walker.

Cada memoria (CPG, CKG, Delta, Trace) se registra como un indice.
CognitiveGraph unifica: search (textual + estructurada), store, get, delete,
schema, metrics, y navegacion de relaciones entre nodos del grafo.

Almacenamiento: cada indice es un nx.DiGraph (nodos + edges) con un
TokenIndex opcional para busqueda full-text sobre campos especificos.

Uso:
    cg = CognitiveGraph(chain_dir)
    cg.search("ckg", {"type": "refactor"})
    cg.search("delta", {"text": "autenticacion"})
    cg.search_many({"delta": {"text": "auth"}, "cpg": {"file": "src/auth.py"}})
    cg.store("delta", "TASK-042", {"type": "task", "objective": "Fix auth bug", "files": ["src/auth.py"]})
    cg.add_edge("delta:TASK-042", "cpg:src/auth.py", "impacts_file")
    cg.get_related("delta:TASK-042")  # navega edges desde un nodo
"""

from __future__ import annotations
import gzip
import json
import os
import time
from typing import Any

import networkx as nx

from .models import TokenIndex


class IndexSchema:
    """Schema de un indice: fields, types, storage, search config."""

    def __init__(self, config: dict):
        self.fields: dict[str, str] = config.get("fields", {})
        self.types: dict[str, dict] = config.get("types", {})
        storage = config.get("storage", {})
        self.storage_path: str = storage.get("path", "data/index.json.gz")
        self.storage_format: str = storage.get("format", "json.gz")
        self.description: str = config.get("description", "")
        # Config de busqueda full-text
        search_cfg = config.get("search", {})
        self.search_type: str = search_cfg.get("type", "structured")
        self.fulltext_fields: list[str] = search_cfg.get("fields", [])

    def resolve_field(self, query_field: str) -> str:
        return self.fields.get(query_field, query_field)

    def has_fulltext(self) -> bool:
        return self.search_type == "fulltext" and bool(self.fulltext_fields)


class Index:
    """Indice individual: grafo (nx.DiGraph) + schema + storage + searcher opcional.

    Almacena:
      - Nodos: _docs (dict id -> data) + _graph (nx.DiGraph)
      - Edges: _graph edges con atributos
      - Busqueda textual: _token_index (TokenIndex) si el schema lo configura
    """

    def __init__(
        self,
        name: str,
        schema: IndexSchema,
        chain_dir: str,
        search_handler: callable | None = None,
    ):
        self.name = name
        self.schema = schema
        self.chain_dir = chain_dir
        self._search_handler = search_handler
        self._docs: dict[str, dict] = {}
        self._graph: nx.DiGraph = nx.DiGraph()
        self._token_index: TokenIndex | None = (
            TokenIndex() if schema.has_fulltext() else None
        )
        self._loaded = False

    @property
    def _path(self) -> str:
        if os.path.isabs(self.schema.storage_path):
            return self.schema.storage_path
        return os.path.join(self.chain_dir, self.schema.storage_path)

    # ── Persistencia ──

    def _read_gz(self, path: str) -> str:
        """Lee archivo .gz con fallback manual si gzip.decompress falla."""
        raw_bytes = open(path, "rb").read()
        try:
            return gzip.decompress(raw_bytes).decode()
        except Exception:
            pass
        # Fallback: skip 10-byte gzip header, decompress raw deflate
        try:
            import zlib
            return zlib.decompress(raw_bytes[10:], -zlib.MAX_WBITS).decode()
        except Exception:
            pass
        # Ultimo fallback: leer como JSON plano
        try:
            return raw_bytes.decode("utf-8")
        except Exception:
            raise

    def _load(self) -> None:
        if self._loaded:
            return
        path = self._path
        if os.path.exists(path):
            try:
                if path.endswith(".gz"):
                    raw = self._read_gz(path)
                else:
                    raw = open(path).read()
                data = json.loads(raw)
                docs = data.get("nodes", data.get("docs", data.get("hits", [])))
                if isinstance(docs, list):
                    self._docs = {
                        d.get("id", d.get("_id", f"doc-{i}")): d
                        for i, d in enumerate(docs)
                    }
                elif isinstance(docs, dict):
                    self._docs = docs
                # Restaurar nodos docs en el grafo (con atributos)
                for doc_id, doc_data in self._docs.items():
                    self._graph.add_node(doc_id, **doc_data)
                # Restaurar edges en el grafo
                for edge in data.get("edges", []):
                    u = edge["from"]
                    v = edge["to"]
                    attrs = {k: v for k, v in edge.items() if k not in ("from", "to")}
                    self._graph.add_edge(u, v, **attrs)
                # Restaurar token index
                ti_data = data.get("token_index")
                if ti_data and self._token_index is not None:
                    self._token_index = TokenIndex.from_dict(ti_data)
            except Exception:
                self._docs = {}
                self._graph = nx.DiGraph()
                self._token_index = TokenIndex() if self.schema.has_fulltext() else None
        self._loaded = True

    def _save(self) -> None:
        path = self._path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "index": self.name,
            "docs": [{"_id": doc_id, **doc} for doc_id, doc in self._docs.items()],
            "edges": [
                {"from": u, "to": v, **data}
                for u, v, data in self._graph.edges(data=True)
            ],
        }
        if self._token_index is not None:
            data["token_index"] = self._token_index.to_dict()
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if path.endswith(".gz"):
            open(path, "wb").write(gzip.compress(payload.encode()))
        else:
            open(path, "w").write(payload)

    # ── CRUD ──

    def store(self, doc_id: str, data: dict) -> dict:
        """Indexa o actualiza un nodo en el grafo.

        Si el schema tiene fulltext configurado, tokeniza los campos
        especificados y los agrega al TokenIndex.
        """
        self._load()
        # Eliminar entries previos si ya existia
        if doc_id in self._docs and self._token_index is not None:
            self._token_index.remove_node(doc_id)
        # Guardar en docs + grafo
        self._docs[doc_id] = data
        self._graph.add_node(doc_id, **data)
        # Indexar campos full-text
        if self._token_index is not None:
            for field in self.schema.fulltext_fields:
                value = data.get(field, "")
                if isinstance(value, str) and value:
                    self._token_index.index_node(doc_id, value, field)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            self._token_index.index_node(doc_id, item, field)
        self._save()
        return {"ok": True, "_id": doc_id, "_index": self.name}

    def store_many(self, docs: dict[str, dict]) -> dict:
        """Indexa muchos nodos en UNA sola carga + UNA sola escritura.

        Evita el O(n²) de store() individual (que recarga y reescribe
        el índice completo por cada nodo). Para sincronizaciones masivas
        (ej: sync CPG -> CognitiveGraph con miles de nodos).
        """
        self._load()
        for doc_id, data in docs.items():
            if doc_id in self._docs and self._token_index is not None:
                self._token_index.remove_node(doc_id)
            self._docs[doc_id] = data
            self._graph.add_node(doc_id, **data)
            if self._token_index is not None:
                for field in self.schema.fulltext_fields:
                    value = data.get(field, "")
                    if isinstance(value, str) and value:
                        self._token_index.index_node(doc_id, value, field)
                    elif isinstance(value, list):
                        for item in value:
                            if isinstance(item, str):
                                self._token_index.index_node(doc_id, item, field)
        self._save()
        return {"ok": True, "stored": len(docs), "_index": self.name}

    def get(self, doc_id: str) -> dict | None:
        self._load()
        doc = self._docs.get(doc_id)
        if doc is None:
            return None
        return {"_id": doc_id, "_index": self.name, "_source": doc}

    def delete(self, doc_id: str) -> dict:
        self._load()
        existed = doc_id in self._docs
        self._docs.pop(doc_id, None)
        if doc_id in self._graph:
            self._graph.remove_node(doc_id)
        if self._token_index is not None:
            self._token_index.remove_node(doc_id)
        if existed:
            self._save()
        return {"ok": True, "deleted": existed, "_id": doc_id, "_index": self.name}

    # ── Edges ──

    def add_edge(self, from_id: str, to_id: str, **attrs) -> dict:
        """Agrega un edge entre dos nodos.

        Args:
            from_id: ID del nodo origen
            to_id: ID del nodo destino
            **attrs: atributos del edge (type, weight, etc.)

        Returns:
            dict con resultado
        """
        self._load()
        self._graph.add_edge(from_id, to_id, **attrs)
        self._save()
        return {"ok": True, "from": from_id, "to": to_id, "attrs": attrs}

    def remove_edge(self, from_id: str, to_id: str) -> dict:
        """Elimina un edge."""
        self._load()
        if self._graph.has_edge(from_id, to_id):
            self._graph.remove_edge(from_id, to_id)
            self._save()
        return {"ok": True, "from": from_id, "to": to_id}

    def get_edges(self, node_id: str, direction: str = "both") -> list[dict]:
        """Retorna todos los edges de un nodo.

        Args:
            node_id: ID del nodo
            direction: "out" (salientes), "in" (entrantes), "both"

        Returns:
            Lista de edges: {"from": ..., "to": ..., "type": ..., ...}
        """
        self._load()
        edges = []
        if direction in ("out", "both"):
            for _, v, data in self._graph.out_edges(node_id, data=True):
                edges.append({"from": node_id, "to": v, **data})
        if direction in ("in", "both"):
            for u, _, data in self._graph.in_edges(node_id, data=True):
                edges.append({"from": u, "to": node_id, **data})
        return edges

    def get_related(self, node_id: str, depth: int = 1) -> dict:
        """Obtiene el subgrafo de contexto alrededor de un nodo.

        Args:
            node_id: Nodo central
            depth: Profundidad de navegacion (0 = solo el nodo)

        Returns:
            dict con nodos y edges del subgrafo
        """
        self._load()
        if node_id not in self._graph:
            return {"center": node_id, "nodes": [], "edges": []}

        visited: set[str] = {node_id}
        current: set[str] = {node_id}
        for _ in range(depth):
            neighbors: set[str] = set()
            for n in current:
                for _, v in self._graph.out_edges(n):
                    if v not in visited:
                        neighbors.add(v)
                for u, _ in self._graph.in_edges(n):
                    if u not in visited:
                        neighbors.add(u)
            visited.update(neighbors)
            current = neighbors

        nodes = []
        for n in visited:
            data = dict(self._graph.nodes.get(n, {}))
            data["_id"] = n
            if ":" in n:
                data["_index"] = n.split(":")[0]
            nodes.append(data)

        edges = []
        for u, v, data in self._graph.edges(data=True):
            if u in visited and v in visited:
                edges.append({"from": u, "to": v, **data})

        return {"center": node_id, "nodes": nodes, "edges": edges, "total": len(nodes)}

    # ── Search ──

    def search(self, query: Any) -> dict:
        """Busca en el indice.

        Soporta:
          - structured: {"type": "refactor", "file": "src/auth.py"}
          - fulltext:   {"text": "autenticacion"}
          - combinado:  {"text": "auth", "type": "task"}

        Si el indice tiene search_handler (CPG legacy), delega en el.
        Si no, busca nativamente usando TokenIndex + grafo.
        """
        if self._search_handler:
            return self._search_handler(query)
        self._load()
        took = time.time()
        filters = query if isinstance(query, dict) else {}

        # Extraer query de texto
        text_query = ""
        remaining = dict(filters)
        if "text" in remaining:
            text_query = remaining.pop("text", "")

        # 1. Busqueda full-text via TokenIndex
        text_matches: set[str] = set()
        if text_query and self._token_index is not None:
            for entry in self._token_index.search(text_query, max_results=50):
                text_matches.add(entry.node_id)

        # 2. Filtro estructurado sobre _docs
        results = []
        for doc_id, doc in self._docs.items():
            # Si hay text_query, solo considerar nodos que matcheen
            if text_query and doc_id not in text_matches:
                continue
            # Aplicar filtros estructurados
            if remaining and not self._match(doc, remaining):
                continue
            # Obtener vecinos del grafo para contexto
            related = self._get_neighbor_ids(doc_id, depth=1) if text_query else []
            entry = {
                "_id": doc_id,
                "_index": self.name,
                "_score": 1.0,
                "_source": doc,
            }
            if related:
                entry["_related"] = related
            results.append(entry)

        took = int((time.time() - took) * 1000)
        return {
            "took": took,
            "index": self.name,
            "hits": {"total": len(results), "hits": results[:50]},
            "schema": self._schema_info(),
        }

    def _get_neighbor_ids(self, node_id: str, depth: int = 1) -> list[dict]:
        """Obtiene IDs de vecinos para incluir como contexto en resultados."""
        neighbors = []
        if node_id not in self._graph:
            return neighbors
        for _, v, data in self._graph.out_edges(node_id, data=True):
            vdata = self._graph.nodes.get(v, {})
            neighbors.append({
                "id": v,
                "rel": data.get("type", "related"),
                "label": vdata.get("objective") or vdata.get("name") or v,
            })
        for u, _, data in self._graph.in_edges(node_id, data=True):
            udata = self._graph.nodes.get(u, {})
            neighbors.append({
                "id": u,
                "rel": data.get("type", "related") + " (incoming)",
                "label": udata.get("objective") or udata.get("name") or u,
            })
        return neighbors

    def _match(self, doc: dict, filters: dict) -> bool:
        """Verifica si un documento coincide con los filtros estructurados."""
        if not filters:
            return True
        for qfield, qvalue in filters.items():
            rfield = self.schema.resolve_field(qfield)
            if rfield == "type":
                if doc.get("type") != qvalue:
                    return False
            elif "." in rfield:
                parts = rfield.split(".")
                val = doc
                for p in parts:
                    val = val.get(p, {}) if isinstance(val, dict) else {}
                if str(qvalue) not in str(val):
                    return False
            else:
                doc_val = doc.get(rfield, "")
                if isinstance(doc_val, str):
                    if qvalue.lower() not in doc_val.lower():
                        return False
                elif isinstance(doc_val, list):
                    if not any(qvalue.lower() in str(item).lower() for item in doc_val):
                        return False
                elif doc_val != qvalue:
                    return False
        return True

    def schema_info(self) -> dict:
        self._load()
        return self._schema_info()

    def _schema_info(self) -> dict:
        types = {}
        for doc in self._docs.values():
            t = doc.get("type", "unknown")
            types[t] = types.get(t, 0) + 1
        return {
            "fields": self.schema.fields,
            "types": types,
            "total_docs": len(self._docs),
            "total_edges": self._graph.number_of_edges(),
            "description": self.schema.description,
        }

    def metrics(self) -> dict:
        self._load()
        path = self._path
        size = os.path.getsize(path) if os.path.exists(path) else 0
        return {
            "total_docs": len(self._docs),
            "total_edges": self._graph.number_of_edges(),
            "storage_size": f"{size / 1024:.1f}KB",
            "storage_path": path,
            "last_updated": time.ctime(os.path.getmtime(path))
            if os.path.exists(path)
            else "never",
        }

    def rebuild_token_index(self) -> None:
        """Limpia y reconstruye el TokenIndex desde los docs actuales."""
        if self._token_index is None:
            return
        self._load()
        self._token_index = TokenIndex()
        for doc_id, doc in self._docs.items():
            for field in self.schema.fulltext_fields:
                value = doc.get(field, "")
                if isinstance(value, str) and value:
                    self._token_index.index_node(doc_id, value, field)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            self._token_index.index_node(doc_id, item, field)
        self._save()


# ═══════════════════════════════════════════════════════════════════
#  CognitiveGraph — Facade
# ═══════════════════════════════════════════════════════════════════

class CognitiveGraph:
    """Facade unificada. Los dominios registran sus indices aqui."""

    def __init__(self, chain_dir: str, auto_register: bool = True):
        self.chain_dir = chain_dir
        self._indexes: dict[str, Index] = {}
        if auto_register:
            self.register_defaults()

    def register_defaults(self) -> None:
        """Registra los 4 indices por defecto: ckg, cpg, delta, trace.

        CKG usa storage interno nativo (con CognitiveGraph.store()).
        CPG usa search_handler que lee CpgFileRepository (legacy).
        Delta usa storage interno nativo + fulltext search.
        Trace usa storage interno nativo.
        """
        # CKG - storage nativo en CognitiveGraph
        self.register_index(
            "ckg",
            {
                "fields": {
                    "type": "type",
                    "scope": "scope",
                    "label": "statement",
                    "task": "metadata.task_id",
                    "description": "rationale",
                    "tag": "tags",
                },
                "types": {
                    "decision": {"description": "Decision de arquitectura"},
                    "refactor": {"description": "Refactorizacion aplicada"},
                    "convention": {"description": "Convencion de estilo"},
                },
                "storage": {"path": "data/ckg.json.gz", "format": "json.gz"},
                "description": "CKG: Knowledge Graph - decisiones, convenciones, patrones",
            },
        )

        # CPG - search_handler que consulta CpgFileRepository (legacy)
        def _cpg_search(query):
            from features.code_analysis.infrastructure.repository import CpgFileRepository

            repo = CpgFileRepository(os.path.join(self.chain_dir, "data"))
            g = repo.load_graph()
            results = []
            filters = query or {}
            for nid, node in g.nodes.items():
                match = True
                for k, v in filters.items():
                    if k == "function" and v not in node.name:
                        match = False
                    elif k == "file" and v not in node.path:
                        match = False
                    elif k == "type" and node.type != v:
                        match = False
                    elif k == "signature" and v not in (node.signature or ""):
                        match = False
                    elif k == "kind" and v not in (node.kind or ""):
                        match = False
                if match:
                    results.append(
                        {
                            "_id": nid,
                            "_source": {
                                "name": node.name,
                                "type": node.type,
                                "file": node.path,
                                "line": node.line,
                                "signature": node.signature,
                                "kind": node.kind,
                            },
                        }
                    )
            return {
                "took": 0,
                "index": "cpg",
                "hits": {"total": len(results), "hits": results[:50]},
                "schema": {},
            }

        self.register_index(
            "cpg",
            {
                "fields": {"function": "name", "file": "path", "type": "type"},
                "types": {"function": {}, "class": {}, "file": {}, "module": {}},
                "storage": {"path": "data/cpg.json.gz", "format": "json.gz"},
                "description": "CPG: Code Property Graph - estructura del codigo fuente",
            },
            search_handler=_cpg_search,
        )

        # CPGs de referencia
        self._register_ref_indexes()

        # Delta - storage nativo en CognitiveGraph + fulltext search
        self.register_index(
            "delta",
            {
                "fields": {"task": "task_id", "domain": "domain", "type": "type"},
                "types": {"task": {}, "sprint": {}},
                "storage": {"path": "data/delta.json.gz", "format": "json.gz"},
                "description": "DeltaGraph: tareas y sprints del proyecto",
                "search": {
                    "type": "fulltext",
                    "fields": ["objective", "title", "description", "domain", "result_state"],
                },
            },
        )

        # Trace - storage nativo en CognitiveGraph
        self.register_index(
            "trace",
            {
                "fields": {
                    "task": "task_id",
                    "tool": "tool",
                    "type": "event_type",
                    "agent": "agent_id",
                    "session": "session_id",
                    "sprint": "sprint_id",
                    "node": "node",
                    "sub_state": "sub_state",
                    "source": "source",
                    "file": "files",
                },
                "types": {
                    "workflow_state": {},
                    "task_execute": {},
                    "agent_message": {},
                    "tool_call": {},
                    "file_impact": {},
                    "kg_query": {},
                },
                "storage": {"path": "data/trace.json.gz", "format": "json.gz"},
                "description": "TraceGraph: memoria de ejecucion (workflow + agente + tools)",
            },
        )

    def _register_ref_indexes(self) -> None:
        """Descubre y registra CPGs de referencia (data/cpg.ref.*.json.gz)."""
        refs_dir = os.path.join(self.chain_dir, "data")
        if not os.path.isdir(refs_dir):
            return
        for fname in os.listdir(refs_dir):
            if not fname.startswith("cpg.ref.") or not fname.endswith(".json.gz"):
                continue
            ref_name = fname.replace("cpg.ref.", "").replace(".json.gz", "")
            index_name = f"cpg-ref-{ref_name}"
            if index_name in self._indexes:
                continue

            ref_path = os.path.join(refs_dir, fname)
            ref_search = self._make_ref_search(ref_path)
            self.register_index(
                index_name,
                {
                    "fields": {"function": "name", "file": "path", "type": "type"},
                    "types": {"function": {}, "class": {}, "file": {}, "module": {}},
                    "storage": {"path": fname, "format": "json.gz"},
                    "description": f"CPG Referencia: {ref_name}",
                },
                search_handler=ref_search,
            )

    @staticmethod
    def _make_ref_search(ref_path: str):
        """Crea un search_handler para un CPG de referencia."""

        def search(query):
            import gzip
            import json

            try:
                raw = gzip.decompress(open(ref_path, "rb").read()).decode()
                data = json.loads(raw)
            except Exception:
                return {"took": 0, "hits": {"total": 0, "hits": []}, "schema": {}}

            nodes = data.get("nodes", {})
            filters = query or {}
            results = []
            for nid, n in nodes.items():
                match = True
                for k, v in filters.items():
                    if k == "function" and v not in n.get("name", ""):
                        match = False
                    elif k == "file" and v not in n.get("path", ""):
                        match = False
                    elif k == "type" and n.get("type") != v:
                        match = False
                    elif k == "class" and v not in n.get("name", ""):
                        match = False
                if match:
                    results.append(
                        {
                            "_id": nid,
                            "_source": {
                                "name": n.get("name"),
                                "type": n.get("type"),
                                "file": n.get("path"),
                                "line": n.get("line", 0),
                                "signature": n.get("signature", ""),
                                "kind": n.get("kind", ""),
                            },
                        }
                    )
            return {
                "took": 0,
                "hits": {"total": len(results), "hits": results[:50]},
                "schema": {},
            }

        return search

    def register_index(
        self, name: str, config: dict, search_handler: callable | None = None
    ) -> Index:
        """Registra un indice con su schema.

        Args:
            name: nombre del indice (ckg, cpg, delta, trace)
            config: schema del indice (fields, types, storage, description, search)
            search_handler: funcion opcional para busqueda custom.
                           Si se provee, reemplaza la busqueda default.
                           Util para CPG legacy que tiene su propio backend.

        Returns:
            El indice registrado.
        """
        schema = IndexSchema(config)
        index = Index(name, schema, self.chain_dir, search_handler)
        self._indexes[name] = index
        return index

    def _get_index(self, name: str) -> Index:
        idx = self._indexes.get(name)
        if not idx:
            raise KeyError(
                f"Indice '{name}' no registrado. Disponibles: {list(self._indexes.keys())}"
            )
        return idx

    # ── Queries ──

    def search(self, index: str, query: Any = None) -> dict:
        """Consulta un indice."""
        return self._get_index(index).search(query or {})

    def search_many(self, queries: dict[str, Any]) -> dict:
        """Consulta multiples indices.

        Args:
            queries: {index_name: query_dict, ...}

        Returns:
            {took, responses: {index_name: result, ...}}
        """
        took = time.time()
        responses = {}
        for name, query in queries.items():
            try:
                responses[name] = self.search(name, query)
            except Exception as e:
                responses[name] = {"ok": False, "error": str(e), "index": name}
        return {
            "took": int((time.time() - took) * 1000),
            "responses": responses,
        }

    # ── CRUD ──

    def store(self, index: str, doc_id: str, data: dict) -> dict:
        return self._get_index(index).store(doc_id, data)

    def store_many(self, index: str, docs: dict[str, dict]) -> dict:
        """Indexa muchos nodos en batch (una sola escritura)."""
        return self._get_index(index).store_many(docs)

    def get(self, index: str, doc_id: str) -> dict | None:
        return self._get_index(index).get(doc_id)

    def delete(self, index: str, doc_id: str) -> dict:
        return self._get_index(index).delete(doc_id)

    # ── Edges ──

    def add_edge(self, from_id: str, to_id: str, **attrs) -> dict:
        """Agrega un edge entre dos nodos.

        Busca en todos los indices cual contiene el nodo origen y
        guarda el edge ahi. Si no lo encuentra, lo guarda en delta.

        Args:
            from_id: ID del nodo origen (ej: "sprintX:TASK-042")
            to_id: ID del nodo destino (ej: "src/auth_middleware.py")
            **attrs: atributos del edge (type="impacts_file", etc.)
        """
        # Encontrar indice que contiene el nodo origen
        source_idx_name = self._find_node_index(from_id)
        if not source_idx_name:
            source_idx_name = "delta"  # fallback

        idx = self._indexes.get(source_idx_name)
        if idx is None:
            return {"ok": False, "error": f"Index '{source_idx_name}' not found",
                    "from": from_id, "to": to_id}

        result = idx.add_edge(from_id, to_id, **attrs)
        return {
            "ok": True,
            "from": from_id,
            "to": to_id,
            "attrs": attrs,
            "index": source_idx_name,
            "result": result,
        }

    def _find_node_index(self, node_id: str) -> str | None:
        """Busca en que indice esta almacenado un nodo por su ID."""
        for name, idx in self._indexes.items():
            try:
                if not idx._loaded:
                    idx._load()
                if node_id in idx._docs:
                    return name
            except Exception:
                continue
        return None

    def get_related(self, node_id: str, depth: int = 1) -> dict:
        """Obtiene el subgrafo de contexto alrededor de un nodo.

        Busca en todos los indices hasta encontrar el nodo.
        """
        source_idx_name = self._find_node_index(node_id)
        if source_idx_name:
            return self._indexes[source_idx_name].get_related(node_id, depth)

        # Si no se encuentra, buscar en todos los grafos
        for name, idx in self._indexes.items():
            if node_id in idx._graph:
                return idx.get_related(node_id, depth)
        return {"center": node_id, "nodes": [], "edges": [], "total": 0}

    # ── Schema / Metrics ──

    def schema(self, index: str) -> dict:
        return self._get_index(index).schema_info()

    def metrics(self, index: str) -> dict:
        return self._get_index(index).metrics()

    def list_indexes(self) -> list[str]:
        return list(self._indexes.keys())
