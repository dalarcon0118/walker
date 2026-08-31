"""CognitiveGraph — STORAGE ENGINE genérico (grafo + token index + backends).

SPLIT de la semántica del walker (ADR-WALKER-067 §7.2): este módulo es el
storage genérico reutilizable (search, store, get, delete, schema, edges).
La SEMÁNTICA de las memorias del walker (schemas ckg/delta/trace/ref, search
handler del trace, proyección/ranking) vive en las features dueñas (forFeature,
ADR-068) y se INYECTA vía `CognitiveGraph(register_defaults=<callable>)`
(p.ej. `app.memories.registration.apply_memory_indexes`).

Almacenamiento: cada indice es un nx.DiGraph (nodos + edges) con un
TokenIndex opcional para busqueda full-text sobre campos especificos.

Uso (genérico):
    cg = CognitiveGraph(chain_dir, register_defaults=apply_memory_indexes)
    cg.register_index("mi_indice", {...})
    cg.search("mi_indice", {"text": "x"})
    cg.store("mi_indice", "ID", {...})
    cg.add_edge("a", "b", "rel")
    cg.get_related("a")
"""

from __future__ import annotations
import json
import os
import time
from typing import Any

from .connection import MemoryConnection
from .file_backend import FileBackend
from .index import Index, IndexSchema
from .ports import StorageBackend
# ═══════════════════════════════════════════════════════════════════
#  CognitiveGraph — Facade
# ═══════════════════════════════════════════════════════════════════

class CognitiveGraph:
    """Facade unificada. Los dominios registran sus indices aqui.

    ADR-WALKER-061 (F3): construccion via (backend, conn). La conexion
    (MemoryConnection) resuelve el directorio base; el backend (StorageBackend)
    es el dueno logico del storage. El schema del indice NO lleva 'storage'
    (el filename se deriva del nombre: {name}.json.gz).
    """

    def __init__(
        self,
        chain_dir: str = "",
        *,
        backend: StorageBackend | None = None,
        conn: MemoryConnection | None = None,
        register_defaults: callable | None = None,
        compact_fields: dict | None = None,
        severity_order: dict | None = None,
    ):
        self.chain_dir = chain_dir
        self._conn = conn
        # Resolucion del directorio base (prioridad):
        #   backend._base_dir > conn.uri > chain_dir
        if backend is not None and getattr(backend, "_base_dir", None):
            self._base_dir: str = backend._base_dir
        elif conn is not None:
            self._base_dir = FileBackend._resolve_base_dir(conn)
        else:
            self._base_dir = chain_dir
        # ADR-WALKER-061 (F3 corregido): CognitiveGraph ES un StorageBackend
        # que DELEGA a un backend concreto. El backend es el dueño real del
        # storage. Si no se inyecta, se construye un FileBackend por defecto
        # (comportamiento backward-compatible).
        if backend is None:
            backend = FileBackend(MemoryConnection(uri="file://" + os.path.abspath(self._base_dir)))
        self._backend: StorageBackend = backend
        # Mapa ligero de schemas (solo metadatos, para ref/trace checks).
        self._indexes: dict[str, IndexSchema] = {}
        # Proyección/ranking: por defecto vacíos (storage genérico). El walker
        # los inyecta (app/memory_schemas.py) — negocio, no del storage.
        self.compact_fields: dict = compact_fields or {}
        self.severity_order: dict = severity_order or {}
        if register_defaults is not None:
            register_defaults(self)

    def get_index(self, name: str) -> Index:
        """Acceso público al Index interno del backend (uso de custom handlers)."""
        return self._backend._get_index(name)  # noqa: SLF001

    def index_docs(self, name: str) -> dict[str, dict]:
        """Docs del indice (cargados) para custom search handlers."""
        idx = self.get_index(name)
        idx._load()  # noqa: SLF001
        return dict(idx._docs)  # noqa: SLF001

    def register_index(
        self, name: str, config: dict, search_handler: callable | None = None
    ) -> IndexSchema:
        """Registra un indice con su schema (delega al backend StorageBackend).

        ADR-WALKER-061 (F3 corregido): el backend es el dueño del storage.
        CognitiveGraph conserva solo un mapa ligero de schemas para checks
        de ref/trace. El search_handler (si existe) se pasa al backend.
        """
        schema = IndexSchema(config)
        self._backend.register_index(name, schema, search_handler)
        self._indexes[name] = schema
        return schema

    def _get_index(self, name: str) -> Index:
        """Acceso al Index interno del backend (usado por handlers de trace/ref)."""
        return self._backend._get_index(name)  # noqa: SLF001

    # ── Queries ──

    def search(self, index: str, query: Any = None) -> dict:
        """Consulta un indice."""
        return self.get_index(index).search(query or {})

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
                key=lambda h: self.self.severity_order.get(
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
        fields = self.compact_fields.get(index)
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
        return self._backend.store(index, doc_id, data)

    def store_many(self, index: str, docs: dict[str, dict]) -> dict:
        """Indexa muchos nodos en batch (una sola escritura)."""
        return self._backend.store_many(index, docs)

    def replace_blob(
        self, index: str, nodes: dict[str, dict], edges: list[dict], metadata: dict
    ) -> dict:
        """Reemplaza TODO el índice en un solo ciclo atómico (F2)."""
        return self._backend.replace_blob(index, nodes, edges, metadata)

    def set_metadata(self, index: str, metadata: dict) -> dict:
        """Persiste metadata de dominio junto a docs/edges (D5)."""
        return self._backend.set_metadata(index, metadata)

    def sync_edges(self, index: str, edges: list[dict]) -> dict:
        """Reemplaza TODOS los edges del indice (sync completo)."""
        return self._backend.sync_edges(index, edges)

    def get_docs(self, index: str) -> dict[str, dict]:
        return self._backend.get_docs(index)

    def get_all_edges(self, index: str) -> list[dict]:
        return self._backend.get_all_edges(index)

    def get_metadata(self, index: str) -> dict:
        return self._backend.get_metadata(index)

    def get(self, index: str, doc_id: str) -> dict | None:
        return self._backend.get(index, doc_id)

    def keys(self, index: str) -> list[str]:
        """Devuelve TODOS los IDs de un indice (sin limites de search)."""
        return self._backend.keys(index)

    def max_key(self, index: str, prefix: str = "") -> tuple[str, int] | None:
        """Devuelve (id, numero) del ID con mayor numero para un prefijo."""
        return self._backend.max_key(index, prefix)

    def delete(self, index: str, doc_id: str) -> dict:
        return self._backend.delete(index, doc_id)

    def delete_many(self, index: str, doc_ids: set[str]) -> dict:
        """Elimina varios nodos en batch (1 lock + 1 save)."""
        return self._backend.delete_many(index, doc_ids)

    def upsert(self, index: str, key: str, data: dict) -> dict:
        """Upsert por key (primitiva reusable de infraestructura).

        Distingue create de update: si el nodo ya existe, hace merge de
        campos (los existentes se preservan salvo que data los pise);
        si no existe, lo crea. Retorna {'created': bool, 'updated': bool,
        'result': ...}.

        Base para amplificaciones de dominio como upsert_spec del
        DeltaGraph (que matchea por campo de negocio ademas de key).
        """
        existing = self.get(index, key)
        if existing:
            merged = dict(existing.get("_source") or {})
            merged.update(data)
            result = self.store(index, key, merged)
            return {"created": False, "updated": True, "result": result}
        result = self.store(index, key, data)
        return {"created": True, "updated": False, "result": result}

    # ── Edges ──

    def add_edge(self, from_id: str, to_id: str, **attrs) -> dict:
        """Agrega un edge entre dos nodos (delega al backend, cross-index)."""
        return self._backend.add_edge(from_id, to_id, **attrs)

    def remove_edge(self, from_id: str, to_id: str) -> dict:
        return self._backend.remove_edge(from_id, to_id)

    def get_edges(self, node_id: str, direction: str = "both") -> list:
        return self._backend.get_edges(node_id, direction)

    def get_related(self, node_id: str, depth: int = 1) -> dict:
        """Obtiene el subgrafo de contexto alrededor de un nodo (cross-index)."""
        return self._backend.get_related(node_id, depth)

    def find_node_index(self, node_id: str) -> str | None:
        """Resuelve qué índice contiene un nodo (lookup cross-index)."""
        return self._backend.find_node_index(node_id)

    # ── Schema / Metrics ──

    def schema_info(self, index: str) -> dict:
        return self._backend.schema_info(index)

    def schema(self, index: str) -> dict:
        """Alias público de schema_info (compat)."""
        return self._backend.schema_info(index)

    def metrics(self, index: str) -> dict:
        return self._backend.metrics(index)

    def list_indexes(self) -> list[str]:
        return self._backend.list_indexes()
