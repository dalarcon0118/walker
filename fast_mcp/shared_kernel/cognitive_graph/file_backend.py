"""cognitive_graph/file_backend.py — FileBackend (StorageBackend sobre .json.gz).

ADR-WALKER-061 (F3 corregido): implementación CONCRETA del puerto StorageBackend
sobre archivos .json.gz. Es el DUEÑO REAL del storage: envuelve un Index por
nombre y resuelve la ruta desde MemoryConnection.uri. CognitiveGraph delega
aquí; un futuro DbBackend implementa el mismo puerto y lo reemplaza por config.

El formato en disco es IDÉNTICO al histórico de Index (_save): el blob
contiene docs/edges/metadata/token_index/_type_index/_field_index/schema_hash.
"""

from __future__ import annotations

import os
from typing import Any

from .connection import MemoryConnection
from .index import Index, IndexSchema
from .ports import StorageBackend


class FileBackend:
    """StorageBackend sobre archivos .json.gz (multi-índice).

    Encapsula la resolución de ruta desde MemoryConnection.uri y mantiene
    un Index por nombre (lazy). Implementa el puerto StorageBackend al
    completo, incluyendo lookup cross-index (find_node_index) y search_handler.
    """

    def __init__(self, conn: MemoryConnection):
        self._conn = conn
        self._base_dir = self._resolve_base_dir(conn)
        self._schemas: dict[str, IndexSchema] = {}
        self._indexes: dict[str, Index] = {}

    @staticmethod
    def _resolve_base_dir(conn: MemoryConnection) -> str:
        uri = conn.uri
        if uri.startswith("file://"):
            return os.path.abspath(uri[len("file://"):])
        return os.path.abspath(uri)

    # ── Registro ──

    def register_index(self, name: str, schema: Any, search_handler: Any = None) -> None:
        if isinstance(schema, dict):
            schema = IndexSchema(schema)
        self._schemas[name] = schema
        # Index perezoso: se construye al primer acceso (necesita base_dir).
        self._indexes[name] = Index(name, schema, self._base_dir, search_handler)

    def _get_index(self, name: str) -> Index:
        idx = self._indexes.get(name)
        if idx is None:
            raise KeyError(
                f"Indice '{name}' no registrado. Disponibles: {list(self._indexes.keys())}"
            )
        return idx

    # ── CRUD doc-level ──

    def store(self, index: str, doc_id: str, data: dict) -> dict:
        return self._get_index(index).store(doc_id, data)

    def store_many(self, index: str, docs: dict[str, dict]) -> dict:
        return self._get_index(index).store_many(docs)

    def get(self, index: str, doc_id: str) -> dict | None:
        return self._get_index(index).get(doc_id)

    def delete(self, index: str, doc_id: str) -> dict:
        return self._get_index(index).delete(doc_id)

    def delete_many(self, index: str, doc_ids: set[str]) -> dict:
        return self._get_index(index).delete_many(doc_ids)

    def max_key(self, index: str, prefix: str = "") -> tuple[str, int] | None:
        return self._get_index(index).max_key(prefix)

    def search(self, index: str, query: dict) -> dict:
        return self._get_index(index).search(query)

    # ── Edges / grafo (cross-index) ──

    def find_node_index(self, node_id: str) -> str | None:
        """Resuelve qué índice contiene un nodo (lookup cross-index)."""
        for name, idx in self._indexes.items():
            try:
                if not idx._loaded:  # noqa: SLF001 (acceso controlado)
                    idx._load()
                if node_id in idx._docs:
                    return name
            except Exception:
                continue
        return None

    def add_edge(self, from_id: str, to_id: str, **attrs: Any) -> dict:
        idx_name = self.find_node_index(from_id) or "delta"
        idx = self._indexes.get(idx_name)
        if idx is None:
            return {"ok": False, "error": f"Index '{idx_name}' not found",
                    "from": from_id, "to": to_id}
        result = idx.add_edge(from_id, to_id, **attrs)
        return {
            "ok": True, "from": from_id, "to": to_id,
            "attrs": attrs, "index": idx_name, "result": result,
        }

    def remove_edge(self, from_id: str, to_id: str) -> dict:
        idx_name = self.find_node_index(from_id) or "delta"
        idx = self._indexes.get(idx_name)
        if idx is None:
            return {"ok": False, "error": f"Index '{idx_name}' not found"}
        return idx.remove_edge(from_id, to_id)

    def get_edges(self, node_id: str, direction: str = "both") -> list:
        name = self.find_node_index(node_id)
        if name:
            return self._indexes[name].get_edges(node_id, direction)
        return []

    def get_related(self, node_id: str, depth: int = 1) -> dict:
        name = self.find_node_index(node_id)
        if name:
            return self._indexes[name].get_related(node_id, depth)
        for name, idx in self._indexes.items():
            if node_id in idx._graph:  # noqa: SLF001
                return idx.get_related(node_id, depth)
        return {"center": node_id, "nodes": [], "edges": [], "total": 0}

    # ── Blob / metadata de dominio ──

    def replace_blob(self, index: str, nodes: dict, edges: list, metadata: dict) -> dict:
        return self._get_index(index).replace_blob(nodes, edges, metadata)

    def sync_edges(self, index: str, edges: list[dict]) -> dict:
        return self._get_index(index).sync_edges(edges)

    def set_metadata(self, index: str, metadata: dict) -> dict:
        return self._get_index(index).set_metadata(metadata)

    def get_metadata(self, index: str) -> dict:
        return self._get_index(index).get_metadata()

    def get_metadata_only(self, index: str) -> dict:
        return self._get_index(index).get_metadata_only()

    def get_docs(self, index: str) -> dict[str, dict]:
        return self._get_index(index).get_docs()

    def get_all_edges(self, index: str) -> list[dict]:
        return self._get_index(index).get_all_edges()

    def keys(self, index: str) -> list[str]:
        return self._get_index(index).keys()

    # ── Puerto elevado StorageDb (ADR-063 §7.1) ──
    # FileBackend persiste en cada write (sin buffer), así que append/compact/
    # flush cubren el contrato elevado durante la convivencia pre-cutover.
    # Con sqlite activo, append = 1 INSERT + COMMIT (ver SqliteBackend).

    def append(self, index: str, docs: dict[str, dict]) -> dict:
        """1 write + COMMIT por llamada (equivalente a store_many en blob)."""
        return self.store_many(index, docs)

    def compact(self, index: str) -> dict:
        """No-op: el formato blob no acumula log que compactar."""
        return {"ok": True, "index": index, "compacted": 0}

    def flush(self, index: str) -> dict:
        """No-op: FileBackend persiste en cada write."""
        return {"ok": True, "index": index, "flushed": 0}

    # ── Primitivas de infra / introspección ──

    def upsert(self, index: str, key: str, data: dict) -> dict:
        existing = self.get(index, key)
        if existing:
            merged = dict(existing.get("_source") or {})
            merged.update(data)
            result = self.store(index, key, merged)
            return {"created": False, "updated": True, "result": result}
        result = self.store(index, key, data)
        return {"created": True, "updated": False, "result": result}

    def schema_info(self, index: str) -> dict:
        return self._get_index(index).schema_info()

    def metrics(self, index: str) -> dict:
        return self._get_index(index).metrics()

    def list_indexes(self) -> list[str]:
        return list(self._indexes.keys())

    # ── Inspección (tests) ──

    def path_for(self, name: str) -> str:
        return self._get_index(name)._path  # noqa: SLF001
