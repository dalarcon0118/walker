"""app/memory_schemas.py — Semántica de las memorias del walker (CPG/CKG/Delta/Trace).

SPLIT de shared/libs/cognitive_graph (ADR-WALKER-067 §7.2 / refinamiento libs):
`shared/libs/cognitive_graph` queda como STORAGE ENGINE genérico (grafo, token
index, backends, ODM, searcher). La SEMÁNTICA de las memorias del walker
(schemas ckg/delta/trace/ref, search handler del trace, proyección COMPACT_FIELDS,
ranking SEVERITY_ORDER, trace descentralizado del proyecto) es NEGOCIO del walker
y vive AQUÍ (capa host/dominio), inyectada al grafo genérico vía
`register_default_memory_indexes(graph)`.
"""
from __future__ import annotations

import gzip
import json
import os
from typing import Any


# ── Proyección/ranking de las memorias (walkers semantics, no del storage) ──

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


# ── Trace: search handler + colección (walker semantics) ──────────────────


def _prepare_trace_filters(filters: dict) -> tuple[str, dict, int | None]:
    """Separa text_query / metacampos de control / filtros estructurados."""
    text_query = ""
    remaining = dict(filters)
    if "text" in remaining:
        text_query = remaining.pop("text", "").lower()
    max_results = (
        remaining.pop("max_results", None)
        or remaining.pop("size", None)
        or remaining.pop("max_events", None)
    )
    remaining.pop("_index", None)
    es_query = remaining.pop("query", None)
    if isinstance(es_query, dict):
        _ = es_query.get("match_all") if isinstance(es_query, dict) else None
    return text_query, remaining, max_results


def _filter_trace_events(events: list[dict], text_query: str, remaining: dict) -> list[dict]:
    """Aplica filtros de texto y estructurados sobre los eventos de trace."""
    results = []
    alias_map = {
        "task": "task_id", "tool": "tool", "type": "event_type",
        "agent": "agent_id", "session": "session_id", "sprint": "sprint_id",
        "node": "node", "sub_state": "sub_state", "source": "source",
        "file": "files",
    }
    for item in events:
        doc = item.get("_source", {})
        match = True
        if text_query:
            haystack = " ".join(str(v) for v in doc.values()).lower()
            if text_query not in haystack:
                match = False
        if match and remaining:
            for k, v in remaining.items():
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


def _collect_project_native_trace(graph: Any, events: list, seen_ids: set) -> None:
    """Recolecta eventos del storage nativo del proyecto real (descentralizado).

    La fuente de verdad del trace es <repo_path>/.cognitive/projects/
    {pid}/data/trace.json.gz, donde repo_path es la referencia que el
    walker central mantiene en data/projects.json. get_project_dir()
    resuelve esa ruta (registry → repo_path), con fallback al .cognitive
    global del walker si el proyecto no tiene repo registrado.
    """
    from app.path_resolver import get_project_dir

    try:
        project_id = os.path.basename(graph.chain_dir)
        proj_dir = get_project_dir(project_id)
        if proj_dir:
            idx_path = proj_dir / "data" / "trace.json.gz"
            if idx_path.is_file():
                with gzip.open(idx_path, "rt") as f:
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


def _collect_trace_events(graph: Any) -> tuple[list[dict], set[str]]:
    """Recolecta eventos de trace: storage nativo + proyecto real (descentralizado)."""
    events = []
    seen_ids: set[str] = set()
    docs = graph.index_docs("trace")
    for doc_id, doc in docs.items():
        if doc_id and doc_id in seen_ids:
            continue
        if doc_id:
            seen_ids.add(doc_id)
        events.append({"_id": doc_id, "_source": doc})
    _collect_project_native_trace(graph, events, seen_ids)
    return events, seen_ids


def _make_trace_search_handler(graph: Any):
    """Crea el handler de búsqueda del índice Trace (semántica walker)."""

    def _trace_search(query):
        filters = query or {}
        events, seen_ids = _collect_trace_events(graph)
        text_query, remaining, max_results = _prepare_trace_filters(filters)
        results = _filter_trace_events(events, text_query, remaining)
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
                    "task": "task_id", "tool": "tool", "type": "event_type",
                    "agent": "agent_id", "session": "session_id",
                    "sprint": "sprint_id", "node": "node", "sub_state": "sub_state",
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


# ── Ref indexes (CPG de referencia) ────────────────────────────────────────


def _make_ref_search(ref_path: str):
    """Crea un search_handler para un CPG de referencia."""

    def search(query):
        try:
            raw = gzip.decompress(open(ref_path, "rb").read()).decode()
            data = json.loads(raw)
        except Exception:
            return {"took": 0, "hits": {"total": 0, "hits": []}, "schema": {}}

        nodes = data.get("nodes", {})
        filters = query or {}
        max_results = (
            filters.get("max_results") or filters.get("size") or filters.get("max_events")
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


def _register_ref_indexes(graph: Any) -> None:
    """Descubre y registra CPGs de referencia (data/cpg.ref.*.json.gz)."""
    refs_dir = os.path.join(graph.chain_dir, "data")
    if not os.path.isdir(refs_dir):
        return
    for fname in os.listdir(refs_dir):
        if not fname.startswith("cpg.ref.") or not fname.endswith(".json.gz"):
            continue
        ref_name = fname.replace("cpg.ref.", "").replace(".json.gz", "")
        index_name = f"cpg-ref-{ref_name}"
        ref_path = os.path.join(refs_dir, fname)
        ref_search = _make_ref_search(ref_path)
        graph.register_index(
            index_name,
            {
                "fields": {"function": "name", "file": "path", "type": "type"},
                "types": {"function": {}, "class": {}, "file": {}, "module": {}},
                "storage": {"path": fname, "format": "json.gz"},
                "description": f"CPG Referencia: {ref_name}",
            },
            search_handler=ref_search,
        )


# ── Registro de las memorias del walker (schemas) ──────────────────────────


def register_default_memory_indexes(graph: Any) -> None:
    """Registra las memorias del walker (ckg/delta/trace + ref) en el grafo.

    Es el reemplazo de `CognitiveGraph.register_defaults` (antes hardcodeado en
    el storage engine). El walker inyecta esta semántica vía
    `CognitiveGraph(register_defaults=register_default_memory_indexes)`.
    """
    graph.register_index(
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
    graph.register_index(
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
    graph.register_index(
        "trace",
        {
            "fields": {
                "task": "task_id", "tool": "tool", "type": "event_type",
                "agent": "agent_id", "session": "session_id",
                "sprint": "sprint_id", "node": "node", "sub_state": "sub_state",
                "source": "source", "file": "files",
            },
            "types": {
                "workflow_state": {}, "task_execute": {},
                "agent_message": {}, "tool_call": {},
                "file_impact": {}, "kg_query": {},
            },
            "description": "TraceGraph: memoria de ejecucion (workflow + agente + tools)",
        },
        search_handler=_make_trace_search_handler(graph),
    )
    _register_ref_indexes(graph)