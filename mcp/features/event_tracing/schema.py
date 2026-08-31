"""features/event_tracing/schema.py — Schema del índice Trace (forFeature, ADR-068).

SSOT: el schema + search handler + recolección descentralizada del TraceGraph
viven SOLO aquí (feature dueña). Reconciliado de las 2 fuentes previas: se
conserva la versión de `app/memory_schemas` (la única con el search handler
custom — filtros de texto/estructurados + trace descentralizado del proyecto).

Nota (ADR-055): el trace se consulta ESTRUCTURADO (type/tool/session/task), no
fulltext (inviable con 56k docs). El handler custom aplica el filtrado.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Proyección: campos que el LLM recibe al compactar resultados Trace.
TRACE_COMPACT_FIELDS = [
    "_id",
    "event_type",
    "timestamp",
    "task_id",
    "session_id",
    "tool",
    "data",
]


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
    {pid}/data/trace.json.gz. La resolución de paths es del HOST
    (app.path_resolver) — edge features→app documentado (ADR-068 P6).
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
    except Exception as e:
        # K: la pérdida del trace descentralizado NO se silencia.
        logger.error("trace descentralizado: no se pudo recolectar %s: %s", graph.chain_dir, e)


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


def register_trace_index(graph: Any) -> None:
    """Registra el índice Trace (schema + search handler) en el storage genérico."""
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


# Auto-registro por import (ADR-068): el module.py de la feature importa este
# módulo; al importarlo se registra el schema del Trace. Sin lista central.
from shared.libs.module_system.global_registry import register_memory_index

register_memory_index(register_trace_index)