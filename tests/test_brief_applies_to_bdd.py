"""test_brief_applies_to_bdd.py — F2a: contexto determinista por archivo.

El problema medido: `brief(file=fast_mcp/memories/handlers/knowledge.py)` citaba
ADR-057/051/022/045/049 (arquitectura genérica) porque el ranking era solo por
texto. Una decisión que declara `applies_to` debe ir primero cuando el archivo
que se está tocando está en esa lista.

Contrato:
  - `documenting_knowledge` persiste `metadata.applies_to` (lista normalizada),
    desde `applies_to` explícito o desde `files`.
  - `brief` expone `decisions[].applies_to` y `decisions[].matched`.
  - Las decisiones que matchean el archivo (o su directorio) van primero; las
    que no declaran `applies_to` no se descartan, se marcan `matched: false`.
"""

from __future__ import annotations

from types import SimpleNamespace

from returns.result import Success

import fast_mcp.composition as composition
import fast_mcp.features.tasks.side_effects.knowledge as side_effects
import fast_mcp.memories.handlers.knowledge as knowledge
from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
    CkgReadService,
)
from fast_mcp.memories.handlers.knowledge import handle_brief


class _Ctx:
    chain_dir = "/tmp/proyecto-applies"
    project_root = "/tmp/proyecto-applies"
    project_id = "walker"


def _node(i: int, applies=None, raw=None) -> dict:
    meta = {"status": "accepted"}
    if applies is not None:
        meta["applies_to"] = applies
    node = {
        "id": f"adr-{i}",
        "statement": f"ADR {i}",
        "rationale": "porque " + "x" * 20,
        "metadata": meta,
    }
    if raw is not None:
        node["applies_to"] = raw
    return node


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
        lambda _ctx, **_: {"files": [{"file": target, "callers": 3, "heaviest": []}],
                           "tests": [], "unmapped": []},
    )
    monkeypatch.setattr(CkgReadService, "gather_context",
                        staticmethod(lambda *_a, **_k: Success(nodes)))
    plan = {"task": "SPEC-027", "acceptance": ["a"], "scope": [target], "next": "verificar"}
    monkeypatch.setattr(
        composition,
        "build_sprint_service",
        lambda _d: SimpleNamespace(
            get_session=lambda _sid: {"spring_id": "SPR-1", "flight_plan": plan},
            get_session_plan=lambda _sid: plan,
            get_specs=lambda _spr: [],
        ),
    )
    monkeypatch.setattr(composition, "workflow_tool_runner",
                        lambda _n: (lambda **_kw: {"current_position": "IMPLEMENT.WORK"}))
    _Ctx.chain_dir = f"/tmp/proyecto-applies-{id(nodes)}"
    _Ctx.project_root = _Ctx.chain_dir


def _brief(target: str = "fast_mcp/memories/handlers/knowledge.py") -> dict:
    return handle_brief(_Ctx(), _payload={"file": target, "session_id": "ses_x"})


def test_la_decision_que_declara_el_archivo_va_primero(monkeypatch):
    _wire(monkeypatch, [
        _node(0),
        _node(1, applies=["fast_mcp/memories/handlers/knowledge.py"]),
        _node(2),
    ])

    out = _brief()
    ids = [d["id"] for d in out["decisions"]]

    assert ids[0] == "adr-1"
    assert out["decisions"][0]["matched"] is True
    assert out["decisions"][0]["applies_to"] == ["fast_mcp/memories/handlers/knowledge.py"]
    assert all(d["matched"] is False for d in out["decisions"][1:])


def test_un_directorio_declarado_tambien_matchea(monkeypatch):
    _wire(monkeypatch, [_node(0, applies=["fast_mcp/memories"]), _node(1)])

    out = _brief()

    assert out["decisions"][0]["id"] == "adr-0"
    assert out["decisions"][0]["matched"] is True


def test_applies_to_en_el_nodo_o_como_string(monkeypatch):
    _wire(monkeypatch, [
        _node(0, raw="fast_mcp/memories/handlers/knowledge.py, otra.py"),
        _node(1),
    ])

    out = _brief()

    assert out["decisions"][0]["id"] == "adr-0"
    assert out["decisions"][0]["applies_to"] == [
        "fast_mcp/memories/handlers/knowledge.py",
        "otra.py",
    ]


def test_sin_match_se_preserva_el_orden_y_se_marca(monkeypatch):
    _wire(monkeypatch, [_node(0), _node(1)], target="otro/archivo.py")

    out = _brief("otro/archivo.py")

    assert [d["id"] for d in out["decisions"]] == ["adr-0", "adr-1"]
    assert all(d["matched"] is False for d in out["decisions"])


def test_documenting_knowledge_persiste_applies_to(monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(
        side_effects,
        "_save_nodes",
        lambda _chain, nodes, _task, _sid: captured.extend(nodes) or {"ok": True},
    )

    out = side_effects.documenting_knowledge(
        "/tmp/proyecto",
        {"applies_to": ["fast_mcp/memories/handlers/knowledge.py"], "nodes": [{"label": "x"}]},
    )

    assert out["ok"] is True
    assert captured[0]["metadata"]["applies_to"] == [
        "fast_mcp/memories/handlers/knowledge.py"
    ]


def test_documenting_knowledge_usa_files_si_falta_applies_to(monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(
        side_effects,
        "_save_nodes",
        lambda _chain, nodes, _task, _sid: captured.extend(nodes) or {"ok": True},
    )

    side_effects.documenting_knowledge(
        "/tmp/proyecto", {"files": ["a.py", "./b.py"], "nodes": [{"label": "x"}]}
    )

    assert captured[0]["metadata"]["applies_to"] == ["a.py", "b.py"]
