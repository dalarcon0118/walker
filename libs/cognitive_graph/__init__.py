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
        """Verifica si un documento coincide con los filtros estructurados.

        RESOLUCIÓN DE CAMPOS: si el valor top-level no matchea, intenta
        con metadata.<campo>. Esto cubre datos legados donde el mismo
        campo (ej. scope) vive en top-level en unos docs y en metadata
        en otros — sin esta normalización, esas convenciones/decisiones
        son inconsultables por filtro estructurado.
        """
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
                if not self._val_contains(val, qvalue):
                    return False
            else:
                doc_val = doc.get(rfield, "")
                if not self._val_contains(doc_val, qvalue):
                    # Fallback: campo legado viviendo en metadata
                    meta_val = (doc.get("metadata") or {}).get(rfield)
                    if not self._val_contains(meta_val, qvalue):
                        return False
        return True

    @staticmethod
    def _val_contains(val, qvalue) -> bool:
        """¿El valor contiene el query (case-insensitive)?"""
        if val is None:
            return False
        if isinstance(val, str):
            return qvalue.lower() in val.lower()
        if isinstance(val, list):
            return any(qvalue.lower() in str(item).lower() for item in val)
        return val == qvalue

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

        # Trace - storage nativo + search_handler que combina NDJSON legacy
        def _trace_search(query):
            """Busca en trace: storage nativo (trace.json.gz) + NDJSON legacy (data/traces/)."""
            import gzip as _gzip

            filters = query or {}
            events = []
            # Dedupe global por _id: el mismo evento puede vivir en el storage
            # nativo Y en el registry global (.cognitive/projects/{pid}/data/) —
            # cuando chain_dir == project_root, ambas fuentes apuntan al MISMO
            # archivo y sin este dedupe cada evento se devuelve duplicado.
            seen_ids: set[str] = set()

            # 1. Storage nativo (trace.json.gz via Index._load)
            idx = self._get_index("trace")
            idx._load()
            for doc_id, doc in idx._docs.items():
                if doc_id and doc_id in seen_ids:
                    continue
                if doc_id:
                    seen_ids.add(doc_id)
                events.append({"_id": doc_id, "_source": doc})

            # 2. NDJSON legacy en data/traces/*.json.gz
            # Busca en: chain_dir, y en el storage real del proyecto
            # (resuelto via registry → repo_path, descentralizado).
            traces_dirs = [os.path.join(self.chain_dir, "data", "traces")]
            try:
                from libs.path_resolver import get_project_dir
                project_id = os.path.basename(self.chain_dir)
                proj_dir = get_project_dir(project_id)
                if proj_dir:
                    traces_dirs.append(str(proj_dir / "data" / "traces"))
            except Exception:
                pass

            seen_events = set()
            for traces_dir in traces_dirs:
                if not os.path.isdir(traces_dir):
                    continue
                for fname in sorted(os.listdir(traces_dir)):
                    if not fname.endswith(".gz"):
                        continue
                    try:
                        with _gzip.open(os.path.join(traces_dir, fname), "rt") as f:
                            content = f.read()
                        for line in content.strip().split("\n"):
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                doc = json.loads(line)
                            except Exception:
                                continue
                            if not isinstance(doc, dict):
                                continue
                            # Dedupe por _id (global) antes del key local
                            ndjson_id = doc.get("id", doc.get("_id", ""))
                            if ndjson_id and ndjson_id in seen_ids:
                                continue
                            if ndjson_id:
                                seen_ids.add(ndjson_id)
                            # Dedupe por (timestamp, event_type, tool)
                            dedupe_key = (
                                doc.get("timestamp", ""),
                                doc.get("event_type", ""),
                                doc.get("tool", ""),
                            )
                            if dedupe_key in seen_events:
                                continue
                            seen_events.add(dedupe_key)
                            events.append({"_id": doc.get("id", ""), "_source": doc})
                    except Exception:
                        continue

            # 3. Storage nativo del proyecto real (descentralizado)
            #    La fuente de verdad del trace es <repo_path>/.cognitive/projects/
            #    {pid}/data/trace.json.gz, donde repo_path es la referencia que el
            #    walker central mantiene en data/projects.json. get_project_dir()
            #    resuelve esa ruta (registry → repo_path), con fallback al .cognitive
            #    global del walker si el proyecto no tiene repo registrado.
            try:
                from libs.path_resolver import get_project_dir
                project_id = os.path.basename(self.chain_dir)
                proj_dir = get_project_dir(project_id)
                if proj_dir:
                    idx_path = proj_dir / "data" / "trace.json.gz"
                    if idx_path.is_file():
                        try:
                            with _gzip.open(idx_path, "rt") as f:
                                raw_idx = json.load(f)
                            docs_idx = raw_idx.get("docs", []) if isinstance(raw_idx, dict) else raw_idx
                            for doc in docs_idx:
                                if isinstance(doc, dict):
                                    doc_id = doc.get("_id", "")
                                    # Dedupe global: si este _id ya vino del
                                    # storage nativo (mismo archivo), se omite.
                                    if doc_id and doc_id in seen_ids:
                                        continue
                                    if doc_id:
                                        seen_ids.add(doc_id)
                                    events.append({"_id": doc_id, "_source": doc})
                        except Exception:
                            pass
            except Exception:
                pass

            # 4. Filtrar
            text_query = ""
            remaining = dict(filters)
            if "text" in remaining:
                text_query = remaining.pop("text", "").lower()
            # Metacampos de control: NO son filtros de datos. El searcher soporta
            # paginación/limites via "max_results"/"size"/"max_events"; si quedan
            # en remaining, el filtro estructurado los compara contra cada doc y
            # devuelve 0 hits (ningún doc tiene esos campos). Extraerlos antes.
            max_results = (
                remaining.pop("max_results", None)
                or remaining.pop("size", None)
                or remaining.pop("max_events", None)
            )
            # "_index" es un selector de índice del protocolo CLI, no un filtro.
            remaining.pop("_index", None)
            # "query" es el wrapper ES-like {"match_all": {}} (o full-text):
            # si su valor es un dict (ES semantics) no es un campo de datos.
            es_query = remaining.pop("query", None)
            if isinstance(es_query, dict) and text_query is None:
                # {"match_all": {}} → sin filtro de texto
                es_text = es_query.get("match_all") if isinstance(es_query, dict) else None
                _ = es_text  # match_all = sin restricción

            results = []
            for item in events:
                doc = item.get("_source", {})
                match = True
                if text_query:
                    haystack = " ".join(str(v) for v in doc.values()).lower()
                    if text_query not in haystack:
                        match = False
                if match and remaining:
                    for k, v in remaining.items():
                        # Mapear aliases del schema
                        alias_map = {
                            "task": "task_id", "tool": "tool",
                            "type": "event_type", "agent": "agent_id",
                            "session": "session_id", "sprint": "sprint_id",
                            "node": "node", "sub_state": "sub_state",
                            "source": "source", "file": "files",
                        }
                        field = alias_map.get(k, k)
                        field_val = doc.get(field, "")
                        needle = str(v)
                        if isinstance(field_val, list):
                            # Buscar el valor en cualquier elemento de la lista
                            if not any(needle in str(item) for item in field_val):
                                match = False
                        else:
                            if needle not in str(field_val):
                                match = False
                if match:
                    results.append({
                        "_id": item.get("_id", ""),
                        "_index": "trace",
                        "_score": 1.0,
                        "_source": doc,
                    })
            # Aplicar límite de resultados (max_results/size)
            if max_results:
                results = results[: int(max_results)]

            return {
                "took": 0,
                "index": "trace",
                "hits": {
                    "total": len(results),
                    # Respetar el límite solicitado; por defecto 50 (como search())
                    "hits": results[: max_results or 50],
                },
                "schema": {
                    "fields": {
                        "task": "task_id", "tool": "tool",
                        "type": "event_type", "agent": "agent_id",
                        "session": "session_id", "sprint": "sprint_id",
                        "node": "node", "sub_state": "sub_state",
                        "source": "source", "file": "files",
                    },
                    "types": {
                        "workflow_state": {}, "task_execute": {},
                        "agent_message": {}, "tool_call": {},
                        "file_impact": {}, "kg_query": {},
                    },
                    "description": "TraceGraph: memoria de ejecucion (workflow + agente + tools)",
                },
            }

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
            search_handler=_trace_search,
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
            queries: {index_name: query_dict, ...}. Soporta metacampos de
                     control con prefijo "_" (no se envían como filtros):
                     - _top_k: límite de hits por capa (default 15)
                     - _max_chars: truncado de strings largos (default 400)
                     - _compact: bool — proyección de campos + ranking
                       (default True)

        Returns:
            {took, responses: {index_name: result, ...}} con respuestas
            compactadas: campos proyectados por índice, strings truncados,
            ordenadas por relevancia y limitadas a top_k.
        """
        took = time.time()
        control = {k: v for k, v in (queries or {}).items() if k.startswith("_")}
        layer_queries = {k: v for k, v in (queries or {}).items() if not k.startswith("_")}
        top_k = int(control.get("_top_k", 15))
        max_chars = int(control.get("_max_chars", 400))
        compact = bool(control.get("_compact", True))

        responses = {}
        for name, query in layer_queries.items():
            try:
                result = self.search(name, query)
                if compact:
                    result = self._compact_result(name, result, top_k=top_k, max_chars=max_chars)
                responses[name] = result
            except Exception as e:
                responses[name] = {"ok": False, "error": str(e), "index": name}
        return {
            "took": int((time.time() - took) * 1000),
            "responses": responses,
        }

    # ═══════════════════════════════════════════════════════════════
    # Poda de respuestas (A+B+C): proyección, ranking y presupuesto
    # ═══════════════════════════════════════════════════════════════

    # Campos relevantes por índice (proyección). El LLM recibe solo estos.
    COMPACT_FIELDS = {
        "ckg": ["_id", "type", "subject", "statement", "scope", "severity", "status"],
        "delta": ["_id", "task_id", "title", "objective", "status", "domain", "result_state"],
        "trace": ["_id", "event_type", "timestamp", "task_id", "session_id", "tool"],
        "cpg": ["_id", "name", "type", "file", "line", "signature", "kind"],
    }

    # Orden de severidad para ranking CKG (obligatoria primero).
    SEVERITY_ORDER = {"obligatoria": 0, "recomendada": 1, "informativa": 2}

    def _compact_result(self, index: str, result: dict, top_k: int, max_chars: int) -> dict:
        """Proyecta, rankea y limita los hits de una capa.

        Conserva la estructura ES-like ({took, index, hits: {total, hits},
        schema}) para no romper consumers, pero:
          A) Proyecta _source a COMPACT_FIELDS (sin rationale/metadata/files).
          B) Ordena por relevancia: CKG por severidad, trace/delta por
             timestamp reciente.
          C) Limita hits a top_k; total conserva el conteo real del índice.
        """
        hits = result.get("hits") or {}
        hit_list = hits.get("hits", []) if isinstance(hits, dict) else []
        total = hits.get("total", len(hit_list)) if isinstance(hits, dict) else len(hit_list)

        projected = []
        for h in hit_list:
            if not isinstance(h, dict):
                continue
            src = h.get("_source") or {}
            if not isinstance(src, dict):
                continue
            compact_src = self._project_source(index, src, max_chars)
            projected.append({
                "_id": h.get("_id", ""),
                "_index": h.get("_index", index),
                "_source": compact_src,
            })

        # B) Ranking por relevancia de capa
        if index == "ckg":
            projected.sort(
                key=lambda h: self.SEVERITY_ORDER.get(
                    (h["_source"].get("severity") or "").lower(), 9
                )
            )
        elif index in ("trace", "delta"):
            projected.sort(
                key=lambda h: h["_source"].get("timestamp", ""),
                reverse=True,
            )

        # C) Presupuesto
        projected = projected[:top_k]
        return {
            "took": result.get("took", 0),
            "index": index,
            "hits": {"total": total, "hits": projected},
            "schema": result.get("schema", {}),
        }

    def _project_source(self, index: str, src: dict, max_chars: int) -> dict:
        """Proyecta un documento a los campos relevantes del índice.

        Los campos de la whitelist se truncan a max_chars. El campo 'data'
        de trace se resume (index + query) para evitar traer payloads grandes.
        """
        fields = self.COMPACT_FIELDS.get(index)
        if not fields:
            # Índice sin whitelist: truncar strings largos conservando todo
            return {
                k: (v[:max_chars] + "..." if isinstance(v, str) and len(v) > max_chars else v)
                for k, v in src.items()
            }

        out = {}
        for f in fields:
            if f not in src:
                continue
            v = src[f]
            if isinstance(v, str) and len(v) > max_chars:
                v = v[:max_chars] + "..."
            elif f == "data" and isinstance(v, dict):
                # Trace: resumir payload (index + query acotada)
                q = v.get("query") or {}
                out["data"] = {
                    "index": v.get("index", ""),
                    "query": self._truncate_dict(q, max_chars=160),
                }
                continue
            out[f] = v
        return out

    @staticmethod
    def _truncate_dict(d: dict, max_chars: int) -> dict:
        return {
            k: (v[:max_chars] + "..." if isinstance(v, str) and len(v) > max_chars else v)
            for k, v in d.items()
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
