"""test_decisions_view_bdd.py — Vista 'decisions <archivo>' (SPR-COCKPIT SPEC-005).

Contrato: una vista humana que muestra las decisiones CKG que aplican a un
archivo: id, titulo, estado, applies_to, supersedes. Sin match, lista las del
area. Texto humano, sin JSON, <=30 lineas.
"""
from __future__ import annotations

from types import SimpleNamespace

from returns.result import Success

import fast_mcp.composition as composition
import fast_mcp.memories.handlers.knowledge as knowledge
from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
    CkgReadService,
)
from fast_mcp.memories.handlers.cockpit import handle_decisions_view


class _Ctx:
    chain_dir = "/tmp/proyecto"
    project_root = "/tmp/proyecto"
    project_id = "walker"


def _node(i: int, applies=None, state="accepted") -> dict:
    meta = {"status": state}
    if applies is not None:
        meta["applies_to"] = applies
    return {
        "id": f"adr-{i}",
        "statement": f"ADR {i}",
        "rationale": "porque " + "x" * 20,
        "metadata": meta,
        "supersedes": ["adr-viejo"] if i == 1 else [],
    }


def _wire(monkeypatch, nodes: list[dict], target: str = "fast_mcp/memories/handlers/knowledge.py"):
    knowledge._IMPACT_CACHE.clear()
    knowledge._SELF_CACHE.clear()
    monkeypatch.setattr(
        knowledge,
        "_resolve_anchor",
        lambda *_: ({"type": "file", "value": target, "resolved": True}, [target], None),
    )
    monkeypatch.setattr(
        knowledge,
        "handle_preflight",
        lambda _ctx, **_: {"files": [{"file": target, "callers": 5, "heaviest": []}],
                           "tests": [], "unmapped": []},
    )
    monkeypatch.setattr(CkgReadService, "gather_context",
                        staticmethod(lambda *_a, **_k: Success(nodes)))
    plan = {"task": "SPEC-005", "acceptance": ["a"], "scope": [target], "next": "verificar"}
    monkeypatch.setattr(
        composition,
        "build_sprint_service",
        lambda _d: SimpleNamespace(
            get_session=lambda _sid: {"spring_id": "SPR-1", "flight_plan": plan},
            get_session_plan=lambda _sid: plan,
            get_specs=lambda _spr: [],
            list_sessions=lambda **_kw: [],
        ),
    )
    monkeypatch.setattr(composition, "workflow_tool_runner",
                        lambda _n: (lambda **_kw: {"current_position": "IMPLEMENT.WORK"}))
    _Ctx.chain_dir = f"/tmp/proyecto-dv-{id(nodes)}"
    _Ctx.project_root = _Ctx.chain_dir


def test_decisions_view_muestra_match_y_supersedes(monkeypatch):
    _wire(monkeypatch, [_node(0), _node(1, applies=["fast_mcp/memories/handlers/knowledge.py"]), _node(2)])

    out = handle_decisions_view(_Ctx(), _payload={"file": "fast_mcp/memories/handlers/knowledge.py", "session_id": "ses_x"})

    assert out["type"] == "decisions_view"
    assert out["matched_count"] == 1
    assert "Decisiones que aplican a:" in out["text"]
    assert "adr-1" in out["text"]
    assert "applies_to:" in out["text"]
    assert "supersedes:" in out["text"]
    assert "{" not in out["text"]


def test_decisions_view_sin_match_lista_area(monkeypatch):
    _wire(monkeypatch, [_node(0), _node(1)], target="otro/archivo.py")

    out = handle_decisions_view(_Ctx(), _payload={"file": "otro/archivo.py", "session_id": "ses_x"})

    assert "del área (sin match de archivo)" in out["text"]
    assert "adr-0" in out["text"]


def test_decisions_view_sin_decisiones(monkeypatch):
    _wire(monkeypatch, [])

    out = handle_decisions_view(_Ctx(), _payload={"file": "a.py", "session_id": "ses_x"})

    assert "ninguna decisión recuperada" in out["text"]


def test_decisions_view_capa_30_lineas(monkeypatch):
    _wire(monkeypatch, [_node(i) for i in range(10)])

    out = handle_decisions_view(_Ctx(), _payload={"file": "a.py", "session_id": "ses_x"})

    assert out["lines"] <= 30
    assert len(out["text"]) <= 1200