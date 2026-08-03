"""
Test: PODA DE RESPUESTAS EN search_many (A+B+C).

Problema: kg_multi devolvía 53KB+ de JSON crudo al LLM porque el searcher
no proyectaba campos, no rankeaba y no limitaba por capa.

Fix (compact search):
  A) Proyección: _source se reduce a campos relevantes por índice
     (COMPACT_FIELDS), sin rationale/metadata/files ni payloads grandes.
  B) Ranking: CKG por severidad (obligatoria primero); trace/delta por
     timestamp reciente.
  C) Presupuesto: top_k por capa (default 10 en kg_multi, 15 en el searcher).

Valida:
  1. search_many devuelve hits proyectados (sin campos pesados).
  2. CKG rankea por severidad (obligatoria > recomendada).
  3. trace rankea por timestamp reciente.
  4. top_k limita hits por capa, total conserva el conteo real.
  5. _with_compact_defaults inyecta _top_k/_max_chars/_compact en kg_multi.
  6. La estructura ES-like se conserva (compatibilidad con providers/tests).
"""

import gzip
import json
import os
import tempfile

from libs.cognitive_graph import CognitiveGraph


def _make_project(tmpdir: str) -> str:
    """Proyecto temporal con CKG, delta y trace con datos de prueba."""
    project_root = os.path.join(tmpdir, "proj")
    os.makedirs(os.path.join(project_root, "data"), exist_ok=True)

    ckg_docs = [
        {
            "_id": "conv-oblig",
            "type": "convention",
            "type_": "convention",
            "subject": "Obligatoria",
            "statement": "DDD vertical slice",
            "scope": "architecture",
            "severity": "obligatoria",
            "rationale": "Muy largo —" + "x" * 800,
        },
        {
            "_id": "conv-recom",
            "type": "convention",
            "subject": "Recomendada",
            "statement": "Usar TEA pattern",
            "scope": "global",
            "severity": "recomendada",
        },
        {
            "_id": "conv-info",
            "type": "convention",
            "subject": "Informativa",
            "statement": "Nombres con underscore",
            "scope": "global",
            "severity": "informativa",
        },
    ]
    ckg_payload = {"index": "ckg", "docs": ckg_docs, "edges": []}
    with gzip.open(os.path.join(project_root, "data", "ckg.json.gz"), "wt") as f:
        json.dump(ckg_payload, f, ensure_ascii=False)

    delta_docs = [
        {
            "_id": "T-2",
            "task_id": "T-002",
            "title": "Viejo",
            "objective": "Tarea antigua",
            "status": "done",
            "timestamp": "2026-01-01T00:00:00",
        },
        {
            "_id": "T-1",
            "task_id": "T-001",
            "title": "Reciente",
            "objective": "Tarea nueva",
            "status": "in_progress",
            "timestamp": "2026-08-01T00:00:00",
        },
    ]
    delta_payload = {"index": "delta", "docs": delta_docs, "edges": []}
    with gzip.open(os.path.join(project_root, "data", "delta.json.gz"), "wt") as f:
        json.dump(delta_payload, f, ensure_ascii=False)

    trace_docs = [
        {
            "_id": "ev-old",
            "event_type": "kg_query",
            "timestamp": "2026-01-01T00:00:00",
            "task_id": "",
            "session_id": "cli",
            "tool": "",
            "data": {"index": "ckg", "query": {"type": "convention"}},
        },
        {
            "_id": "ev-new",
            "event_type": "kg_query",
            "timestamp": "2026-08-01T00:00:00",
            "task_id": "",
            "session_id": "cli",
            "tool": "",
            "data": {
                "index": "ckg",
                "query": {"type": "convention", "extra": "y" * 500},
            },
        },
    ]
    trace_payload = {"index": "trace", "docs": trace_docs, "edges": []}
    with gzip.open(os.path.join(project_root, "data", "trace.json.gz"), "wt") as f:
        json.dump(trace_payload, f, ensure_ascii=False)

    return project_root


# ════════════════════════════════════════════════════════════════
# A. Proyección de campos
# ════════════════════════════════════════════════════════════════


def test_search_many_proyecta_campos():
    """search_many no debe traer rationale/metadata (campos pesados)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(_make_project(tmpdir))
        result = cg.search_many({"ckg": {"type": "convention"}})
        hits = result["responses"]["ckg"]["hits"]["hits"]
        assert len(hits) >= 1
        for h in hits:
            src = h["_source"]
            assert "rationale" not in src, f"rationale no proyectado: {src}"
            assert "metadata" not in src
        print("  ✅ test_search_many_proyecta_campos")


def test_search_many_trunca_strings_largos():
    """Strings largos se truncan a max_chars."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(_make_project(tmpdir))
        result = cg.search_many(
            {"ckg": {"type": "convention"}},
        )
        # Con _max_chars chico
        result = cg.search_many(
            {
                "ckg": {"type": "convention"},
                "_max_chars": 50,
            }
        )
        hits = result["responses"]["ckg"]["hits"]["hits"]
        for h in hits:
            stmt = h["_source"].get("statement", "")
            assert len(stmt) <= 53, f"statement sin truncar: {len(stmt)}"
        print("  ✅ test_search_many_trunca_strings_largos")


def test_trace_data_se_resume():
    """El campo data del trace se resume (index + query acotada)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(_make_project(tmpdir))
        result = cg.search_many({"trace": {}})
        hits = result["responses"]["trace"]["hits"]["hits"]
        for h in hits:
            data = h["_source"].get("data")
            if isinstance(data, dict):
                assert set(data.keys()) <= {"index", "query"}, (
                    f"data no resumido: {data.keys()}"
                )
                q = data.get("query") or {}
                for v in q.values():
                    if isinstance(v, str):
                        assert len(v) <= 163, "query sin truncar"
        print("  ✅ test_trace_data_se_resume")


# ════════════════════════════════════════════════════════════════
# B. Ranking por relevancia
# ════════════════════════════════════════════════════════════════


def test_ckg_rankea_por_severidad():
    """CKG: obligatoria antes que recomendada/informativa."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(_make_project(tmpdir))
        result = cg.search_many({"ckg": {"type": "convention"}})
        hits = result["responses"]["ckg"]["hits"]["hits"]
        severities = [h["_source"].get("severity") for h in hits]
        assert severities[0] == "obligatoria", (
            f"primera debe ser obligatoria: {severities}"
        )
        print("  ✅ test_ckg_rankea_por_severidad")


def test_trace_rankea_por_reciente():
    """Trace: el evento más reciente primero."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(_make_project(tmpdir))
        result = cg.search_many({"trace": {}})
        hits = result["responses"]["trace"]["hits"]["hits"]
        assert hits[0]["_source"]["timestamp"] == "2026-08-01T00:00:00", (
            f"reciente debe ir primero: {hits[0]['_source'].get('timestamp')}"
        )
        print("  ✅ test_trace_rankea_por_reciente")


# ════════════════════════════════════════════════════════════════
# C. Presupuesto (top_k)
# ════════════════════════════════════════════════════════════════


def test_top_k_limita_hits_conserva_total():
    """top_k limita hits; total conserva el conteo real del índice."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(_make_project(tmpdir))
        result = cg.search_many(
            {
                "ckg": {"type": "convention"},
                "_top_k": 2,
            }
        )
        resp = result["responses"]["ckg"]
        assert len(resp["hits"]["hits"]) == 2, (
            f"top_k no aplicó: {len(resp['hits']['hits'])}"
        )
        assert resp["hits"]["total"] == 3, f"total debe ser 3: {resp['hits']['total']}"
        print("  ✅ test_top_k_limita_hits_conserva_total")


def test_estructura_eslike_se_conserva():
    """La respuesta mantiene {took, index, hits:{total,hits}, schema}."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(_make_project(tmpdir))
        result = cg.search_many({"ckg": {"type": "convention"}})
        resp = result["responses"]["ckg"]
        assert set(resp.keys()) >= {"took", "index", "hits", "schema"}
        assert set(resp["hits"].keys()) >= {"total", "hits"}
        print("  ✅ test_estructura_eslike_se_conserva")


def test_compact_false_devuelve_completo():
    """_compact=False desactiva la poda (compatibilidad)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(_make_project(tmpdir))
        result = cg.search_many(
            {
                "ckg": {"type": "convention"},
                "_compact": False,
            }
        )
        hits = result["responses"]["ckg"]["hits"]["hits"]
        # Sin poda, el rationale largo debería estar presente
        has_rationale = any("rationale" in h["_source"] for h in hits)
        assert has_rationale, "compact=False no conserva rationale"
        print("  ✅ test_compact_false_devuelve_completo")


# ════════════════════════════════════════════════════════════════
# kg_multi: defaults inyectados
# ════════════════════════════════════════════════════════════════


def test_with_compact_defaults():
    """_with_compact_defaults inyecta _top_k/_max_chars/_compact."""
    from features.task_execution.tasks.kg import _with_compact_defaults

    base = {"ckg": {"type": "convention"}}
    out = _with_compact_defaults(base)
    assert out["_top_k"] == 10
    assert out["_max_chars"] == 400
    assert out["_compact"] is True
    # No pisa valores explícitos del caller
    custom = {"ckg": {}, "_top_k": 5}
    out2 = _with_compact_defaults(custom)
    assert out2["_top_k"] == 5
    # No muta el original
    assert "_top_k" not in base
    print("  ✅ test_with_compact_defaults")


def test_tamano_respuesta_reducido():
    """La respuesta compactada es significativamente menor en bytes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(_make_project(tmpdir))

        full = cg.search_many({"ckg": {"type": "convention"}, "_compact": False})
        compact = cg.search_many({"ckg": {"type": "convention"}, "_compact": True})

        full_bytes = len(json.dumps(full))
        compact_bytes = len(json.dumps(compact))
        assert compact_bytes < full_bytes, (
            f"compact ({compact_bytes}) no es menor que full ({full_bytes})"
        )
        print(
            f"  ✅ test_tamano_respuesta_reducido: {full_bytes} → {compact_bytes} bytes"
        )
