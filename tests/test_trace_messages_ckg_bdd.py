"""tests/test_trace_messages_ckg_bdd.py — T-14 + T-15 (BDD).

T-14 — TraceGraph cumple el propósito de mensajes user/agent (project.md,
taxonomía de memorias):
  - `user_message` es tipo válido del dominio (EventType).
  - La tool `trace_message` persiste event_type 'user_message'/'agent_message'
    con role/text/session_id en data.
  - Fail-fast: role inválido y text vacío NO escriben (error estructurado).

T-15 — Validación de CKG (memoria del conocimiento: decisiones, reglas,
invariantes, convenciones) sobre la implementación real.
"""
from __future__ import annotations

import pytest


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def trace_svc(tmp_path):
    from fast_mcp.shared_kernel.cognitive_graph import build_cognitive_graph
    from fast_mcp.shared_kernel.cognitive_graph.schemas import PRODUCTION_INDEX_SCHEMAS
    from fast_mcp.features.event_tracing.infrastructure.cognitive_trace_repo import (
        CognitiveTraceRepository,
    )
    from fast_mcp.features.event_tracing.application.services.trace_service import (
        TraceService,
    )

    cg = build_cognitive_graph(str(tmp_path))
    if "trace" not in cg.list_indexes():
        cg.register_index("trace", PRODUCTION_INDEX_SCHEMAS["trace"])
    return TraceService(CognitiveTraceRepository(cg))


@pytest.fixture
def ckg(tmp_path):
    from fast_mcp.features.cognitive_knowledge._composition import ckg_service

    svc = ckg_service(str(tmp_path))
    assert svc is not None
    return svc


def _ctx(svc: object) -> dict:
    return {"trace_service": svc, "chain_dir": ""}


# ── T-14: mensajes user/agent en TraceGraph ───────────────────────────


class TestT14UserMessageType:
    def test_user_message_es_tipo_valido_del_dominio(self):
        from fast_mcp.features.event_tracing.domain.values import EventType

        assert EventType("user_message").value == "user_message"
        assert EventType("agent_message").value == "agent_message"


class TestT14TraceMessage:
    def test_user_message_persiste_con_rol_y_sesion(self, trace_svc):
        from fast_mcp.features.event_tracing.handlers.trace import handle_trace_message

        out = handle_trace_message(
            _ctx(trace_svc),
            role="user",
            text="arregla el bug del sprint",
            session_id="s1",
        )
        assert out.get("ok") is True, out

        events = trace_svc.load_traces("")
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == "user_message"
        assert ev.data.get("role") == "user"
        assert ev.data.get("text") == "arregla el bug del sprint"
        assert ev.data.get("session_id") == "s1"

    def test_agent_message_persiste(self, trace_svc):
        from fast_mcp.features.event_tracing.handlers.trace import handle_trace_message

        out = handle_trace_message(_ctx(trace_svc), role="agent", text="553 tests en verde")
        assert out.get("ok") is True, out
        events = trace_svc.load_traces("")
        assert events[0].event_type == "agent_message"
        assert events[0].data.get("role") == "agent"

    def test_role_invalido_no_escribe_fail_fast(self, trace_svc):
        from fast_mcp.features.event_tracing.handlers.trace import handle_trace_message

        out = handle_trace_message(_ctx(trace_svc), role="system", text="hola")
        assert "error" in out
        assert trace_svc.load_traces("") == []

    def test_text_vacio_no_escribe_fail_fast(self, trace_svc):
        from fast_mcp.features.event_tracing.handlers.trace import handle_trace_message

        out = handle_trace_message(_ctx(trace_svc), role="user", text="   ")
        assert "error" in out
        assert trace_svc.load_traces("") == []


# ── T-15: validación de CKG (memoria del conocimiento) ────────────────


def _rows(ckg, filtros: dict | None = None) -> list[dict]:
    return ckg.query(filtros or {})


class TestT15CkgValidate:
    def test_indice_ckg_registrado(self, ckg):
        assert "ckg" in ckg.list_indexes()

    def test_save_decision_y_query_por_type(self, ckg):
        ckg.save_decision(
            "Backend obligatorio en CognitiveGraph",
            description="file no es backend runtime (ADR-063)",
        )
        rows = _rows(ckg, {"type": "decision"})
        assert len(rows) == 1
        stmt = rows[0].get("statement") or rows[0].get("label") or ""
        assert "Backend obligatorio" in stmt

    def test_fulltext_search_por_statement(self, ckg):
        ckg.save_decision("Invariant: delta_tracking es SSOT", description="INV-004")
        ckg.save_nodes([{
            "label": "Convención: Result[T, E] en toda operación",
            "type": "convention",
            "description": "returns library",
        }])
        hits = ckg.search("delta_tracking SSOT")
        assert len(hits) >= 1
        assert any("SSOT" in str(h.get("statement") or h.get("label") or "") for h in hits)

    def test_label_vacio_se_ignora_degradado(self, ckg):
        ckg.save_nodes([{"label": "", "type": "decision"}, {"type": "rule"}])
        assert _rows(ckg) == []

    def test_supersede_enlaza_por_statement(self, ckg):
        ckg.save_decision("Decisión A: backend sqlite")
        ckg.save_nodes([{
            "label": "Decisión A: backend sqlite (revisada)",
            "type": "decision",
            "supersedes": ["backend sqlite"],
        }])
        # El viejo degradado (status=superseded) se excluye del query default
        # → incluir superseded para ver ambos lados del enlace (T-15).
        by_stmt = {
            str(r.get("statement") or r.get("label") or ""): r
            for r in _rows(ckg, {"include_superseded": True})
        }
        nuevo = by_stmt["Decisión A: backend sqlite (revisada)"]
        viejo = by_stmt["Decisión A: backend sqlite"]
        viejo_id = viejo.get("_id") or viejo.get("id")
        assert viejo_id in (nuevo.get("supersedes") or [])
        assert ((viejo.get("metadata") or {}).get("status")) == "superseded"
        assert ((viejo.get("metadata") or {}).get("superseded_by")) == nuevo.get("statement")

    def test_ids_sin_colision(self, ckg):
        for i in range(5):
            ckg.save_nodes([{"label": f"regla {i}", "type": "rule"}])
        ids = [r.get("_id") or r.get("id") for r in _rows(ckg)]
        assert len(ids) == 5
        assert len(set(ids)) == 5

