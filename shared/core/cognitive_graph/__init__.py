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
        auto_register: bool = True,
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
        if auto_register:
            self.register_defaults()

    def register_defaults(self) -> None:
        """Registra los indices por defecto: ckg, delta, trace + ref.

        ADR-WALKER-054 D2: el indice cpg NO se registra aquí — el dominio CPG
        se registra SOLO en el composition root via
        `register_cognitive_indexes()` (libs/cognitive_graph/cognitive_registry).

        CKG usa storage interno nativo (con CognitiveGraph.store()).
        Delta usa storage interno nativo + fulltext search.
        Trace usa storage interno nativo.
        """
        self._register_ckg_index()
        self._register_ref_indexes()
        self._register_delta_index()
        self._register_trace_index()

    def _register_ckg_index(self) -> None:
        """Registra el indice CKG (Knowledge Graph)."""
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
                "description": "CKG: Knowledge Graph - decisiones, convenciones, patrones",
            },
        )

    def _register_delta_index(self) -> None:
        """Registra el indice Delta (specs y springs) con fulltext search.

        SSOT (unificacion Task/Spec): el indice delta contiene docs con
        esquema Spec/Spring (domain/entities.py) — index_service y el
        repositorio escriben Spec.to_dict(). El alias 'task' legacy se
        conserva como alias de spec_id para no romper consultas antiguas.
        """
        self.register_index(
            "delta",
            {
                "fields": {
                    "task": "spec_id",
                    "spec": "spec_id",
                    "spring": "spring_id",
                    "domain": "domain",
                    "type": "type",
                },
                "types": {"spec": {}, "spring": {}},
                "description": "DeltaGraph: specs y springs del proyecto",
                "search": {
                    "type": "fulltext",
                    "fields": ["objective", "title", "description", "domain", "result_state"],
                },
            },
        )

    def _register_trace_index(self) -> None:
        """Registra el indice Trace (memoria de ejecucion)."""
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
                "description": "TraceGraph: memoria de ejecucion (workflow + agente + tools)",
            },
            search_handler=self._make_trace_search_handler(),
        )

    def _register_trace_index_legacy(self) -> None:  # pragma: no cover - remove
        """ELIMINADO en F3: el storage del trace ya no vive en el schema."""
        raise NotImplementedError

    def _make_trace_search_handler(self):
        """Crea el handler de busqueda del indice Trace (storage nativo)."""

        def _trace_search(query):
            """Busca en trace: storage nativo local + proyecto real descentralizado."""
            filters = query or {}
            events, seen_ids = self._collect_trace_events()
            text_query, remaining, max_results = self._prepare_trace_filters(filters)
            results = self._filter_trace_events(events, text_query, remaining)
            _from_raw = filters.get("_from") or filters.get("from") or 0
            try:
                _from = max(int(_from_raw), 0)
            except (TypeError, ValueError):
                _from = 0

            return {
                "took": 0,
                "index": "trace",
                "hits": {
                    "total": len(results),
                    "hits": results[_from : _from + (max_results or 50)],
                    "from": _from,
                    "size": max_results or 50,
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

        return _trace_search

    def _collect_trace_events(self) -> tuple[list[dict], set[str]]:
        """Recolecta eventos de trace desde: storage nativo y storage del
        proyecto real (descentralizado).

        Dedupe global por _id: el mismo evento puede vivir en el storage
        nativo Y en el registry global — cuando chain_dir == project_root,
        ambas fuentes apuntan al MISMO archivo.
        """
        events = []
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

        # 2. Storage nativo del proyecto real (descentralizado)
        self._collect_project_native_trace(events, seen_ids)

        return events, seen_ids

    def _collect_project_native_trace(self, events: list, seen_ids: set) -> None:
        """Recolecta eventos del storage nativo del proyecto real (descentralizado).

        La fuente de verdad del trace es <repo_path>/.cognitive/projects/
        {pid}/data/trace.json.gz, donde repo_path es la referencia que el
        walker central mantiene en data/projects.json. get_project_dir()
        resuelve esa ruta (registry → repo_path), con fallback al .cognitive
        global del walker si el proyecto no tiene repo registrado.
        """
        import gzip as _gzip

        try:
            from shared.core.path_resolver import get_project_dir
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
                                if doc_id and doc_id in seen_ids:
                                    continue
                                if doc_id:
                                    seen_ids.add(doc_id)
                                events.append({"_id": doc_id, "_source": doc})
                    except Exception:
                        pass
        except Exception:
            pass

        return events, seen_ids

    def _prepare_trace_filters(
        self, filters: dict
    ) -> tuple[str, dict, int | None]:
        """Separa text_query / metacampos de control / filtros estructurados."""
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
            _ = es_query.get("match_all") if isinstance(es_query, dict) else None
        return text_query, remaining, max_results

    def _filter_trace_events(
        self, events: list[dict], text_query: str, remaining: dict
    ) -> list[dict]:
        """Aplica filtros de texto y estructurados sobre los eventos."""
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
        return results

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
            # Metacampos de control (max_results/size): no son filtros.
            max_results = (
                filters.get("max_results")
                or filters.get("size")
                or filters.get("max_events")
            )
            try:
                max_results = int(max_results) if max_results else 50
            except (TypeError, ValueError):
                max_results = 50
            skip_keys = {"max_results", "size", "max_events", "_index", "query", "text"}
            results = []
            for nid, n in nodes.items():
                match = True
                for k, v in filters.items():
                    if k in skip_keys:
                        continue
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
                "hits": {"total": len(results), "hits": results[:max_results]},
                "schema": {},
            }

        return search

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
        "delta": ["_id", "spec_id", "spring_id", "objective", "status", "domain", "result_state"],
        "trace": [
            "_id",
            "event_type",
            "timestamp",
            "task_id",
            "session_id",
            "tool",
            "data",
        ],
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
