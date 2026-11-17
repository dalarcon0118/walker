"""tests/test_ckg_compact_rank_bdd.py — ranking por severidad en la capa CKG.

Bug medido 2026-11-16: `_compact_result` ordenaba los hits de CKG con
`self.self.severity_order`, un typo que reventaba con
`'CognitiveGraph' object has no attribute 'self'` SIEMPRE que la búsqueda
devolvía al menos un hit. La firma era invisible: `ckg {text: "cancel"}`
devolvía `{"ok": false, "error": ...}` mientras una búsqueda sin hits
respondía `total: 0` sin error — por eso pasó desapercibido.

Nota: `compact_fields`/`severity_order` los inyecta el caller (el factory
`build_cognitive_graph` no los expone), así que en producción el ranking es
inerte: el test fija AMBAS caras — sin inyección no debe romper, con inyección
debe ordenar.
"""

from __future__ import annotations

from fast_mcp.shared_kernel.cognitive_graph import CognitiveGraph
from fast_mcp.shared_kernel.cognitive_graph.backends import resolve_storage_backend
from fast_mcp.shared_kernel.cognitive_graph.connection import MemoryConnection

_RESULT = {
    "took": 1,
    "hits": {
        "total": 2,
        "hits": [
            {"_id": "a", "_source": {"statement": "leve", "severity": "low"}},
            {"_id": "b", "_source": {"statement": "grave", "severity": "critical"}},
        ],
    },
}


def _graph(tmp_path, **kwargs) -> CognitiveGraph:
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    return CognitiveGraph("", conn=conn, backend=resolve_storage_backend(conn), **kwargs)


def test_compact_result_ckg_sin_severity_order_no_revienta(tmp_path):
    """La config por defecto (sin inyección) no debe lanzar con hits."""
    cg = _graph(tmp_path)
    out = cg._compact_result("ckg", _RESULT, top_k=5, max_chars=200)  # noqa: SLF001
    assert out["hits"]["total"] == 2
    assert len(out["hits"]["hits"]) == 2


def test_compact_result_ckg_ordena_por_severidad_inyectada(tmp_path):
    cg = _graph(tmp_path, severity_order={"low": 8, "high": 1, "critical": 0})
    out = cg._compact_result("ckg", _RESULT, top_k=5, max_chars=200)  # noqa: SLF001
    assert [h["_id"] for h in out["hits"]["hits"]] == ["b", "a"], (
        "CKG debe ordenar critical antes que low cuando severity_order existe"
    )


def test_compact_result_ckg_sin_hits_no_falla(tmp_path):
    cg = _graph(tmp_path)
    out = cg._compact_result(  # noqa: SLF001
        "ckg", {"hits": {"total": 0, "hits": []}}, top_k=5, max_chars=200
    )
    assert out["hits"] == {"total": 0, "hits": []}


def test_factory_inyecta_compact_y_severity(tmp_path):
    """build_cognitive_graph debe inyectar los valores canónicos (schemas.py).

    Antes nadie los pasaba: compact_fields/severity_order quedaban vacíos y el
    ranking por severidad era inerte (`get(..., 9)` siempre 9).
    """
    from fast_mcp.shared_kernel.cognitive_graph import build_cognitive_graph
    from fast_mcp.shared_kernel.cognitive_graph.schemas import (
        COMPACT_FIELDS,
        SEVERITY_ORDER,
    )

    cg = build_cognitive_graph(chain_dir=str(tmp_path))
    assert cg.compact_fields == COMPACT_FIELDS
    assert cg.severity_order == SEVERITY_ORDER
    assert cg.compact_fields["ckg"], "ckg debe tener whitelist de campos"
    assert cg.severity_order["critical"] < cg.severity_order["low"]


def test_ranking_funciona_con_severity_presente(tmp_path):
    """Con severity_order inyectado por el factory, critical va antes que low."""
    from fast_mcp.shared_kernel.cognitive_graph import build_cognitive_graph

    cg = build_cognitive_graph(chain_dir=str(tmp_path))
    result = {
        "hits": {
            "total": 2,
            "hits": [
                {"_id": "a", "_source": {"statement": "leve", "severity": "low"}},
                {"_id": "b", "_source": {"statement": "grave", "severity": "critical"}},
            ],
        }
    }
    out = cg._compact_result("ckg", result, top_k=5, max_chars=200)  # noqa: SLF001
    assert [h["_id"] for h in out["hits"]["hits"]] == ["b", "a"]
