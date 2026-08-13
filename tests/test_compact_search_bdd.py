"""Tests BDD (pytest-bdd) — Poda de respuestas en busquedas.

Feature value: search_many proyecta campos, rankea por relevancia y respeta
top_k para reducir el tamanio de respuestas enviadas al LLM.

Escenarios: bdd/features/compact_search.feature
"""

from __future__ import annotations

import gzip
import json
import os

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/compact_search.feature")


@pytest.fixture
def compact_ctx() -> dict:
    return {}


def _make_project(tmpdir: str) -> str:
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


@given("que existe un proyecto temporal con CKG, delta y trace con datos de prueba")
def _setup_project(compact_ctx: dict, tmp_path):
    project_root = _make_project(str(tmp_path))
    compact_ctx["project_root"] = project_root


def _get_cg(compact_ctx: dict):
    from shared.core.cognitive_graph import CognitiveGraph

    return CognitiveGraph(compact_ctx["project_root"])


@when("ejecuto search_many con query ckg type:convention")
def _search_ckg(compact_ctx: dict):
    cg = _get_cg(compact_ctx)
    compact_ctx["result"] = cg.search_many({"ckg": {"type": "convention"}})


@then("los hits no contienen rationale en _source")
def _assert_no_rationale(compact_ctx: dict):
    hits = compact_ctx["result"]["responses"]["ckg"]["hits"]["hits"]
    assert len(hits) >= 1
    for h in hits:
        src = h["_source"]
        assert "rationale" not in src, f"rationale no proyectado: {src}"


@then("los hits no contienen metadata en _source")
def _assert_no_metadata(compact_ctx: dict):
    hits = compact_ctx["result"]["responses"]["ckg"]["hits"]["hits"]
    for h in hits:
        src = h["_source"]
        assert "metadata" not in src, f"metadata no proyectado: {src}"


@when("ejecuto search_many con _max_chars 50")
def _search_with_max_chars(compact_ctx: dict):
    cg = _get_cg(compact_ctx)
    compact_ctx["result"] = cg.search_many(
        {"ckg": {"type": "convention"}, "_max_chars": 50}
    )


@then("los statement truncados tienen longitud <= 53")
def _assert_truncated(compact_ctx: dict):
    hits = compact_ctx["result"]["responses"]["ckg"]["hits"]["hits"]
    for h in hits:
        stmt = h["_source"].get("statement", "")
        assert len(stmt) <= 53, f"statement sin truncar: {len(stmt)}"


@when("ejecuto search_many sobre trace")
def _search_trace(compact_ctx: dict):
    cg = _get_cg(compact_ctx)
    compact_ctx["result"] = cg.search_many({"trace": {}})


@then("las claves de data estan dentro de {index, query}")
def _assert_trace_data_keys(compact_ctx: dict):
    hits = compact_ctx["result"]["responses"]["trace"]["hits"]["hits"]
    for h in hits:
        data = h["_source"].get("data")
        if isinstance(data, dict):
            assert set(data.keys()) <= {"index", "query"}, (
                f"data no resumido: {data.keys()}"
            )


@then("los valores de query truncados tienen longitud <= 163")
def _assert_query_truncated(compact_ctx: dict):
    hits = compact_ctx["result"]["responses"]["trace"]["hits"]["hits"]
    for h in hits:
        data = h["_source"].get("data")
        if isinstance(data, dict):
            q = data.get("query") or {}
            for v in q.values():
                if isinstance(v, str):
                    assert len(v) <= 163, "query sin truncar"


@then("la primera severidad es obligatoria")
def _assert_ckg_severity_order(compact_ctx: dict):
    hits = compact_ctx["result"]["responses"]["ckg"]["hits"]["hits"]
    severities = [h["_source"].get("severity") for h in hits]
    assert severities[0] == "obligatoria", f"primera debe ser obligatoria: {severities}"


@then("el primer evento tiene el timestamp mas reciente")
def _assert_trace_recent_first(compact_ctx: dict):
    hits = compact_ctx["result"]["responses"]["trace"]["hits"]["hits"]
    assert hits[0]["_source"]["timestamp"] == "2026-08-01T00:00:00", (
        f"reciente debe ir primero: {hits[0]['_source'].get('timestamp')}"
    )


@when("ejecuto search_many con _top_k 2 sobre ckg")
def _search_top_k(compact_ctx: dict):
    cg = _get_cg(compact_ctx)
    compact_ctx["result"] = cg.search_many({"ckg": {"type": "convention"}, "_top_k": 2})


@then("se devuelven 2 hits")
def _assert_top_k_hits(compact_ctx: dict):
    resp = compact_ctx["result"]["responses"]["ckg"]
    assert len(resp["hits"]["hits"]) == 2, (
        f"top_k no aplicó: {len(resp['hits']['hits'])}"
    )


@then("el total del indice sigue siendo 3")
def _assert_total_conserved(compact_ctx: dict):
    resp = compact_ctx["result"]["responses"]["ckg"]
    assert resp["hits"]["total"] == 3, f"total debe ser 3: {resp['hits']['total']}"


@then("la respuesta contiene took, index, hits y schema")
def _assert_es_structure_keys(compact_ctx: dict):
    resp = compact_ctx["result"]["responses"]["ckg"]
    assert set(resp.keys()) >= {"took", "index", "hits", "schema"}


@then("hits contiene total y hits")
def _assert_hits_structure(compact_ctx: dict):
    resp = compact_ctx["result"]["responses"]["ckg"]
    assert set(resp["hits"].keys()) >= {"total", "hits"}


@when("ejecuto search_many con _compact False sobre ckg")
def _search_no_compact(compact_ctx: dict):
    cg = _get_cg(compact_ctx)
    compact_ctx["result"] = cg.search_many(
        {"ckg": {"type": "convention"}, "_compact": False}
    )


@then("algun hit contiene rationale")
def _assert_rationale_present(compact_ctx: dict):
    hits = compact_ctx["result"]["responses"]["ckg"]["hits"]["hits"]
    has_rationale = any("rationale" in h["_source"] for h in hits)
    assert has_rationale, "compact=False no conserva rationale"


@when("se aplica _with_compact_defaults a una query base")
def _apply_compact_defaults(compact_ctx: dict):
    from features.main.task_execution.tasks.kg import _with_compact_defaults

    base = {"ckg": {"type": "convention"}}
    compact_ctx["out"] = _with_compact_defaults(base)


@then("_top_k es 10")
def _assert_default_top_k(compact_ctx: dict):
    assert compact_ctx["out"]["_top_k"] == 10


@then("_max_chars es 400")
def _assert_default_max_chars(compact_ctx: dict):
    assert compact_ctx["out"]["_max_chars"] == 400


@then("_compact es True")
def _assert_default_compact(compact_ctx: dict):
    assert compact_ctx["out"]["_compact"] is True


@then("no pisa valores explicitos del caller")
def _assert_no_override_explicit(compact_ctx: dict):
    custom = {"ckg": {}, "_top_k": 5}
    from features.main.task_execution.tasks.kg import _with_compact_defaults

    out2 = _with_compact_defaults(custom)
    assert out2["_top_k"] == 5, (
        f"_top_k debe respetar valor explicito: {out2['_top_k']}"
    )


@then("no muta el diccionario original")
def _assert_no_mutation(compact_ctx: dict):
    from features.main.task_execution.tasks.kg import _with_compact_defaults

    base = {"ckg": {"type": "convention"}}
    _with_compact_defaults(base)
    assert "_top_k" not in base, "no debe mutar el original"


@when("comparo la respuesta compacta contra la completa")
def _compare_sizes(compact_ctx: dict):
    cg = _get_cg(compact_ctx)
    full = cg.search_many({"ckg": {"type": "convention"}, "_compact": False})
    compact = cg.search_many({"ckg": {"type": "convention"}, "_compact": True})
    compact_ctx["full_bytes"] = len(json.dumps(full))
    compact_ctx["compact_bytes"] = len(json.dumps(compact))


@then("la respuesta compactada tiene menos bytes que la completa")
def _assert_compact_smaller(compact_ctx: dict):
    assert compact_ctx["compact_bytes"] < compact_ctx["full_bytes"], (
        f"compact ({compact_ctx['compact_bytes']}) no es menor que full ({compact_ctx['full_bytes']})"
    )
