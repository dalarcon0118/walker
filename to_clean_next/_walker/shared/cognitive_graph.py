"""CognitiveGraph — Capa de infraestructura tipo ODM para memorias del walker.

Cada memoria (CPG, CKG, Delta, Trace) se registra como un índice.
CognitiveGraph unifica: search, store, get, delete, schema, metrics.

Uso:
    cg = CognitiveGraph(chain_dir)
    cg.search("ckg", {"type": "refactor"})
    cg.search_many({"ckg": {"type": "refactor"}, "cpg": {"function": "cmd_create"}})
    cg.store("ckg", "decision-001", {"type": "decision", "statement": "..."})
"""

from __future__ import annotations
import gzip
import json
import os
import time
from typing import Any


class IndexSchema:
    """Schema de un índice: fields, types, storage."""

    def __init__(self, config: dict):
        self.fields: dict[str, str] = config.get("fields", {})
        self.types: dict[str, dict] = config.get("types", {})
        storage = config.get("storage", {})
        self.storage_path: str = storage.get("path", "data/index.json.gz")
        self.storage_format: str = storage.get("format", "json.gz")
        self.description: str = config.get("description", "")

    def resolve_field(self, query_field: str) -> str:
        """Resuelve un campo de query a su campo real en el documento."""
        return self.fields.get(query_field, query_field)


class Index:
    """Índice individual: datos + schema + storage + searcher opcional."""

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
        self._loaded = False

    @property
    def _path(self) -> str:
        if os.path.isabs(self.schema.storage_path):
            return self.schema.storage_path
        return os.path.join(self.chain_dir, self.schema.storage_path)

    # ── Persistencia ──

    def _load(self) -> None:
        if self._loaded:
            return
        path = self._path
        if os.path.exists(path):
            try:
                if path.endswith(".gz"):
                    raw = gzip.decompress(open(path, "rb").read()).decode()
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
            except Exception:
                self._docs = {}
        self._loaded = True

    def _save(self) -> None:
        path = self._path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {"index": self.name, "docs": list(self._docs.values())}
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if path.endswith(".gz"):
            open(path, "wb").write(gzip.compress(payload.encode()))
        else:
            open(path, "w").write(payload)

    # ── CRUD ──

    def search(self, query: Any) -> dict:
        """Busca documentos que coincidan con todos los filtros.

        Si el índice tiene un search_handler (CPG, Delta, Trace),
        delega en él. Si no (CKG), usa el storage interno.
        """
        if self._search_handler:
            return self._search_handler(query)
        self._load()
        took = time.time()
        filters = query if isinstance(query, dict) else {}
        results = []
        for doc_id, doc in self._docs.items():
            if self._match(doc, filters):
                entry = {
                    "_id": doc_id,
                    "_index": self.name,
                    "_score": 1.0,
                    "_source": doc,
                }
                results.append(entry)

        took = int((time.time() - took) * 1000)
        return {
            "took": took,
            "index": self.name,
            "hits": {"total": len(results), "hits": results[:50]},
            "schema": self._schema_info(),
        }

    def _match(self, doc: dict, filters: dict) -> bool:
        """Verifica si un documento coincide con los filtros."""
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
                elif doc_val != qvalue:
                    return False
        return True

    def store(self, doc_id: str, data: dict) -> dict:
        """Indexa o actualiza un documento."""
        self._load()
        self._docs[doc_id] = data
        self._save()
        return {"ok": True, "_id": doc_id, "_index": self.name}

    def get(self, doc_id: str) -> dict | None:
        """Obtiene un documento por ID."""
        self._load()
        doc = self._docs.get(doc_id)
        if doc is None:
            return None
        return {"_id": doc_id, "_index": self.name, "_source": doc}

    def delete(self, doc_id: str) -> dict:
        """Elimina un documento."""
        self._load()
        existed = doc_id in self._docs
        self._docs.pop(doc_id, None)
        if existed:
            self._save()
        return {"ok": True, "deleted": existed, "_id": doc_id, "_index": self.name}

    def schema_info(self) -> dict:
        """Retorna schema + stats del índice."""
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
            "description": self.schema.description,
        }

    def metrics(self) -> dict:
        """Retorna métricas del índice."""
        self._load()
        path = self._path
        size = os.path.getsize(path) if os.path.exists(path) else 0
        return {
            "total_docs": len(self._docs),
            "storage_size": f"{size / 1024:.1f}KB",
            "storage_path": path,
            "last_updated": time.ctime(os.path.getmtime(path))
            if os.path.exists(path)
            else "never",
        }


class CognitiveGraph:
    """Facade unificada. Los dominios registran sus índices aquí."""

    def __init__(self, chain_dir: str, auto_register: bool = True):
        self.chain_dir = chain_dir
        self._indexes: dict[str, Index] = {}
        if auto_register:
            self.register_defaults()

    def register_defaults(self) -> None:
        """Registra los 4 índices por defecto: ckg, cpg, delta, trace.

        Cada uno con su search_handler que consulta su fuente de datos real.
        CKG usa storage interno. CPG/Delta/Trace usan handlers custom.
        """
        # CKG — storage directo en CognitiveGraph
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
                    "decision": {"description": "Decisión de arquitectura"},
                    "refactor": {"description": "Refactorización aplicada"},
                    "convention": {"description": "Convención de estilo"},
                },
                "storage": {"path": "data/ckg.json.gz", "format": "json.gz"},
                "description": "CKG: Knowledge Graph — decisiones, convenciones, patrones",
            },
        )

        # CPG — search_handler que consulta CpgFileRepository
        def _cpg_search(query):
            from cpg.infrastructure.repository import CpgFileRepository

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
                if match:
                    results.append(
                        {
                            "_id": nid,
                            "_source": {
                                "name": node.name,
                                "type": node.type,
                                "file": node.path,
                                "line": node.line,
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
                "description": "CPG: Code Property Graph — estructura del código fuente",
            },
            search_handler=_cpg_search,
        )

        # CPGs de referencia: data/cpg.ref.<name>.json.gz
        self._register_ref_indexes()

        # Delta — search_handler que usa deltagraph module
        def _delta_search(query):
            try:
                from deltagraph import DeltaGraphService

                svc = DeltaGraphService(self.chain_dir)
                results = (
                    svc.search(str(query.get("objective", query.get("task", "")))[:100])
                    if query
                    else []
                )
                return {
                    "took": 0,
                    "index": "delta",
                    "hits": {
                        "total": len(results),
                        "hits": [
                            {"_id": r.get("source_id", "?"), "_source": r}
                            for r in results[:50]
                        ],
                    },
                    "schema": {},
                }
            except Exception:
                return {
                    "took": 0,
                    "index": "delta",
                    "hits": {"total": 0, "hits": []},
                    "schema": {},
                }

        self.register_index(
            "delta",
            {
                "fields": {"task": "task_id", "domain": "domain", "type": "type"},
                "types": {"sprint": {}, "task": {}},
                "storage": {"path": "data/delta.json.gz", "format": "json.gz"},
                "description": "DeltaGraph: tareas y sprints del proyecto",
            },
            search_handler=_delta_search,
        )

        # Trace — usa storage directo de CognitiveGraph (escrito por TraceLogger)
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
                "description": "TraceGraph: memoria de ejecución (workflow + agente + tools)",
            },
            search_handler=None,
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
                continue  # ya registrado

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
        """Registra un índice con su schema.

        Args:
            name: nombre del índice (ckg, cpg, delta, trace)
            config: schema del índice (fields, types, storage, description)
            search_handler: función opcional para búsqueda custom.
                           Si se provee, reemplaza la búsqueda default.
                           Útil para CPG, Delta, Trace que tienen sus
                           propios backends.

        Returns:
            El índice registrado.
        """
        schema = IndexSchema(config)
        index = Index(name, schema, self.chain_dir, search_handler)
        self._indexes[name] = index
        return index

    def _get_index(self, name: str) -> Index:
        idx = self._indexes.get(name)
        if not idx:
            raise KeyError(
                f"Índice '{name}' no registrado. Disponibles: {list(self._indexes.keys())}"
            )
        return idx

    # ── Queries ──

    def search(self, index: str, query: Any = None) -> dict:
        """Consulta un índice."""
        return self._get_index(index).search(query or {})

    def search_many(self, queries: dict[str, Any]) -> dict:
        """Consulta múltiples índices.

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

    def get(self, index: str, doc_id: str) -> dict | None:
        return self._get_index(index).get(doc_id)

    def delete(self, index: str, doc_id: str) -> dict:
        return self._get_index(index).delete(doc_id)

    # ── Schema / Metrics ──

    def schema(self, index: str) -> dict:
        return self._get_index(index).schema_info()

    def metrics(self, index: str) -> dict:
        return self._get_index(index).metrics()

    def list_indexes(self) -> list[str]:
        return list(self._indexes.keys())
