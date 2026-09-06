"""features/code_analysis/schema.py — Schemas del CPG (forFeature, ADR-068).

SSOT: el schema del índice CPG (Code Property Graph) + los CPGs de referencia
(cpg.ref.*) viven SOLO aquí (feature dueña). Reconciliado de las 2 fuentes:
- cpg → `app/cognitive_registry` (la única fuente; conserva el fix FTS:
  sin el bloque search fulltext, el CPG devolvía 0 hits textuales).
- cpg.ref.* → `app/memory_schemas` (descubrimiento + search handler).
"""
from __future__ import annotations

import gzip
import json
import os
from typing import Any

# Proyección: campos que el LLM recibe al compactar resultados CPG.
CPG_COMPACT_FIELDS = ["_id", "name", "type", "file", "line", "signature", "kind"]


def register_cpg_index(graph: Any) -> None:
    """Registra el índice CPG en el storage genérico (una feature, un índice).

    Schema del SSOT ÚNICO (T-10/M-6: fast_mcp/shared_kernel/cognitive_graph/
    schemas.py) — jamás copia literal. Incluye search.fulltext (FIX FTS sordo:
    sin este bloque has_fulltext()=False → _token_index None → toda búsqueda
    textual devolvía 0 hits aunque los docs existieran).
    """
    from fast_mcp.shared_kernel.cognitive_graph.schemas import (
        PRODUCTION_INDEX_SCHEMAS,
    )

    graph.register_index("cpg", PRODUCTION_INDEX_SCHEMAS["cpg"])


def _make_ref_search(ref_path: str):
    """Crea un search_handler para un CPG de referencia (con cache, B5)."""
    _cache: dict = {}

    def search(query):
        try:
            if "payload" not in _cache:
                raw = gzip.decompress(open(ref_path, "rb").read()).decode()
                _cache["payload"] = json.loads(raw)
            data = _cache["payload"]
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
    """Descubre y registra CPGs de referencia (cpg.ref.*.json.gz).

    W2 (ADR-076): en el layout canónico v3, `graph.chain_dir` YA es el
    data_dir del proyecto (composition.py pasa paths.data_dir), y los refs
    viven directamente en `data_dir/cpg.ref.*.json.gz`
    (walker_cpg_adapter.py:223). El join con "data" era del layout legacy v2
    (chain_dir=repo_root + data/ dentro) y apuntaba a un directorio que no
    existe en v3 → los refs quedaban sin registrar en silencio.
    """
    refs_dir = graph.chain_dir
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


def register_cpg_indexes(graph: Any) -> None:
    """Registra el índice CPG + los CPGs de referencia (una feature, sus índices)."""
    register_cpg_index(graph)
    _register_ref_indexes(graph)
