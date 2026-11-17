"""test_kg_query_evidence_bdd.py — la evidencia que el gate exigía y nadie emitía.

`required_tools` del workflow prueba que el agente consultó el CKG/CPG buscando
un evento `kg_query` en la traza. El tipo estaba declarado en
`event_tracing/domain/values.py` y no había un solo productor, así que el gate
era insatisfacible (medido 2026-11-17: `submit` en IMPLEMENT.WORK bloqueado para
siempre).

La emisión vive en `dispatch` — único punto por donde pasan todas las tools de
memoria — y solo para CONSULTAS. Un write (`save`, `supersede`) o una consulta
que falló no son evidencia de haber mirado nada.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fast_mcp.memories import handlers


@pytest.fixture()
def emitted(monkeypatch):
    """Captura los eventos que dispatch empujaría al trace."""
    events: list[dict] = []

    class _Service:
        def push_event(self, chain_dir, data):
            events.append({"chain_dir": chain_dir, **data})
            return {"ok": True}

    from fast_mcp.features.event_tracing.application.services import trace_factory

    monkeypatch.setattr(trace_factory, "build_trace_service", lambda cd: _Service())
    monkeypatch.setattr(handlers, "_handlers", {})
    return events


def _ctx():
    return SimpleNamespace(chain_dir="/tmp/proyecto")


def _fake(monkeypatch, name, result):
    monkeypatch.setitem(handlers._handlers, name, lambda ctx, **kw: result)


def test_recall_knowledge_emite_kg_query(monkeypatch, emitted):
    _fake(monkeypatch, "recall_knowledge", {"hits": {"total": 3}})

    handlers.dispatch("recall_knowledge", _ctx(), index="ckg", query="x")

    assert len(emitted) == 1
    assert emitted[0]["event_type"] == "kg_query"
    assert emitted[0]["data"]["tool"] == "recall_knowledge"
    assert emitted[0]["data"]["layers"] == ["ckg"]


def test_knowledge_query_solo_emite_en_reads(monkeypatch, emitted):
    _fake(monkeypatch, "knowledge_query", {"action": "search", "result": []})
    handlers.dispatch("knowledge_query", _ctx(), action="search", query="x")
    assert len(emitted) == 1

    _fake(monkeypatch, "knowledge_query", {"action": "save", "result": {}})
    handlers.dispatch("knowledge_query", _ctx(), action="save", nodes=[{"id": "a"}])
    assert len(emitted) == 1, "un save no prueba que se haya consultado nada"


def test_write_tools_no_emiten(monkeypatch, emitted):
    for tool in ("documenting_knowledge", "index_repo", "build_code_index"):
        _fake(monkeypatch, tool, {"ok": True})
        handlers.dispatch(tool, _ctx())
    assert emitted == []


def test_consulta_fallida_no_es_evidencia(monkeypatch, emitted):
    _fake(monkeypatch, "recall_knowledge", {"error": "CKG not available"})

    handlers.dispatch("recall_knowledge", _ctx(), index="ckg")

    assert emitted == []


def test_las_tools_de_codigo_marcan_la_capa_cpg(monkeypatch, emitted):
    _fake(monkeypatch, "code_search", {"results": []})

    handlers.dispatch("code_search", _ctx(), query="x")

    assert emitted[0]["data"]["layers"] == ["cpg"]


def test_un_error_al_trazar_no_rompe_la_lectura(monkeypatch):
    """Trazar es evidencia; perderla no puede romper una consulta."""
    from fast_mcp.features.event_tracing.application.services import trace_factory

    def _boom(cd):
        raise RuntimeError("sqlite bloqueada")

    monkeypatch.setattr(trace_factory, "build_trace_service", _boom)
    monkeypatch.setitem(handlers._handlers, "recall_knowledge", lambda ctx, **kw: {"ok": True})

    assert handlers.dispatch("recall_knowledge", _ctx(), index="ckg") == {"ok": True}


def test_el_tracer_del_workflow_es_inyectable(tmp_path):
    """R1: workflow no importa event_tracing; el tracer entra por el ctor.

    Sin inyección el bundle queda con NoopTracer y los gates que leen la traza
    (`required_tools`, `skill_gate`) no ven nada.
    """
    from fast_mcp.features.workflow.context import WorkflowContext

    class _Tracer:
        def has_kg_query(self, *a, **kw):
            return True

    tracer = _Tracer()
    ctx = WorkflowContext.resolve(str(tmp_path), tracer=tracer)

    assert ctx is not None
    assert ctx.bundle.tracer is tracer
