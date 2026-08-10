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

from .index import Index, IndexSchema
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
        self._register_ckg_index()
        self._register_cpg_index()
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
                "storage": {"path": "data/ckg.json.gz", "format": "json.gz"},
                "description": "CKG: Knowledge Graph - decisiones, convenciones, patrones",
            },
        )

    def _register_cpg_index(self) -> None:
        """Registra el indice CPG con search_handler que lee CpgFileRepository."""
        self.register_index(
            "cpg",
            {
                "fields": {"function": "name", "file": "path", "type": "type"},
                "types": {"function": {}, "class": {}, "file": {}, "module": {}},
                "storage": {"path": "data/cpg.json.gz", "format": "json.gz"},
                "description": "CPG: Code Property Graph - estructura del codigo fuente",
            },
            search_handler=self._make_cpg_search_handler(),
        )

    def _make_cpg_search_handler(self):
        """Crea el handler de busqueda del indice CPG (legacy CpgFileRepository)."""
        from features.code_analysis.infrastructure.repository import CpgFileRepository

        def _cpg_search(query):
            repo = CpgFileRepository(os.path.join(self.chain_dir, "data"))
            g = repo.load_graph()
            results = []
            filters = query or {}
            max_results = (
                filters.get("max_results")
                or filters.get("size")
                or filters.get("max_events")
            )
            try:
                max_results = int(max_results) if max_results else 50
            except (TypeError, ValueError):
                max_results = 50
            _from_raw = filters.get("_from") or filters.get("from") or 0
            try:
                _from = max(int(_from_raw), 0)
            except (TypeError, ValueError):
                _from = 0
            skip_keys = {"max_results", "size", "max_events", "_index", "query",
                         "text", "_from", "from"}
            for nid, node in g.nodes.items():
                match = True
                for k, v in filters.items():
                    if k in skip_keys:
                        continue
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
                "hits": {
                    "total": len(results),
                    "hits": results[_from : _from + max_results],
                    "from": _from,
                    "size": max_results,
                },
                "schema": {},
            }

        return _cpg_search

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
                "storage": {"path": "data/delta.json.gz", "format": "json.gz"},
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
                "storage": {"path": "data/trace.json.gz", "format": "json.gz"},
                "description": "TraceGraph: memoria de ejecucion (workflow + agente + tools)",
            },
            search_handler=self._make_trace_search_handler(),
        )

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
        return self._get_index(index).store(doc_id, data)

    def store_many(self, index: str, docs: dict[str, dict]) -> dict:
        """Indexa muchos nodos en batch (una sola escritura)."""
        return self._get_index(index).store_many(docs)

    def get(self, index: str, doc_id: str) -> dict | None:
        return self._get_index(index).get(doc_id)

    def keys(self, index: str) -> list[str]:
        """Devuelve TODOS los IDs de un indice (sin limites de search)."""
        return self._get_index(index).keys()

    def max_key(self, index: str, prefix: str = "") -> tuple[str, int] | None:
        """Devuelve (id, numero) del ID con mayor numero para un prefijo."""
        return self._get_index(index).max_key(prefix)

    def delete(self, index: str, doc_id: str) -> dict:
        return self._get_index(index).delete(doc_id)

    def delete_many(self, index: str, doc_ids: set[str]) -> dict:
        """Elimina varios nodos en batch (1 lock + 1 save)."""
        return self._get_index(index).delete_many(doc_ids)

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
