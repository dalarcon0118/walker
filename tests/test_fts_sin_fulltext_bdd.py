"""tests/test_fts_sin_fulltext_bdd.py — un índice sin FTS no responde 0 en silencio.

Medido 2026-11-16 en react-native-frontend:

    search_memories {"trace": {"text": "ERROR"}}  -> total 0, err None
    search_memories {"trace": {"event_type": "ERROR"}} -> 22 hits

El schema de `trace` no declara `search` (es estructurado por diseño), así que
el texto caía al path eager y devolvía cero: indistinguible de "no existe".
Ahora pide filtros estructurados y falla ruidoso (IV-6).
"""

from __future__ import annotations

import pytest

from fast_mcp.shared_kernel.cognitive_graph.backends import resolve_storage_backend
from fast_mcp.shared_kernel.cognitive_graph.connection import MemoryConnection
from fast_mcp.shared_kernel.cognitive_graph.schemas import PRODUCTION_INDEX_SCHEMAS
from fast_mcp.shared_kernel.cognitive_graph.storage_errors import StorageContractError

TRACE_DOCS = {
    "t:1": {"event_type": "ERROR", "tool": "kg_search", "node": "start"},
    "t:2": {"event_type": "Submit", "node": "USER_GATE", "task_id": "T-03"},
}


def _backend(tmp_path):
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    backend = resolve_storage_backend(conn)
    for name in ("delta", "trace", "ckg"):
        backend.register_index(name, PRODUCTION_INDEX_SCHEMAS[name])
    backend.store_many("trace", TRACE_DOCS)
    backend.store_many(
        "delta", {"d:1": {"objective": "parsear texto", "type": "spec", "spec_id": "s-1"}}
    )
    return backend


def test_texto_en_indice_sin_fulltext_falla_ruidoso(tmp_path):
    backend = _backend(tmp_path)
    with pytest.raises(StorageContractError) as exc:
        backend.search("trace", {"text": "ERROR"})
    msg = str(exc.value)
    assert "no declara fulltext" in msg
    assert "estructurados" in msg, "el error debe indicar la salida: filtros"
    assert "drop_fts" in msg, "y cómo habilitar fulltext si se quiere"


def test_filtros_estructurados_siguen_resolviendo(tmp_path):
    backend = _backend(tmp_path)
    result = backend.search("trace", {"event_type": "ERROR"})
    assert result["hits"]["total"] == 1


def test_indice_con_fulltext_no_se_ve_afectado(tmp_path):
    backend = _backend(tmp_path)
    assert backend.search("delta", {"text": "parsear"})["hits"]["total"] == 1


def test_search_many_reporta_el_error_por_capa(tmp_path):
    """Federado: la capa que no soporta texto devuelve ok=false, no 0."""
    from fast_mcp.shared_kernel.cognitive_graph import CognitiveGraph

    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    backend = resolve_storage_backend(conn)
    for name in ("trace", "delta"):
        backend.register_index(name, PRODUCTION_INDEX_SCHEMAS[name])
    backend.store_many("trace", TRACE_DOCS)
    cg = CognitiveGraph("", conn=conn, backend=backend)

    out = cg.search_many({"trace": {"text": "ERROR"}, "delta": {"text": "x"}})
    trace_resp = out["responses"]["trace"]
    assert trace_resp.get("ok") is False
    assert "no declara fulltext" in trace_resp.get("error", "")
