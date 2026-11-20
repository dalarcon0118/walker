"""test_brief_bdd.py — `brief` compuesto (AGENT-EFFICIENCY §2.1 / M-01).

Contrato: una llamada devuelve anchor + decisions + impact + gates, y **nunca
presenta una respuesta parcial como completa**: `missing[]` y `degraded` son
obligatorios. Si una fuente cae, el resto sigue y la sección faltante se
declara (escenario 1 de §4).
"""

from __future__ import annotations

from types import SimpleNamespace

from returns.result import Success

import fast_mcp.composition as composition
import fast_mcp.memories.handlers.knowledge as knowledge
from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
    CkgReadService,
)
from fast_mcp.memories.handlers.knowledge import handle_brief


class _Ctx:
    chain_dir = "/tmp/proyecto"
    project_root = "/tmp/proyecto"
    project_id = "walker"

    def ckg(self):
        raise AssertionError("brief no debe resolver el CKG completo")


def _nodes(n: int = 7) -> list[dict]:
    return [
        {
            "id": f"adr-{i}",
            "statement": f"ADR {i}",
            "rationale": "porque " + "x" * 400,
            "metadata": {"status": "accepted"},
            "supersedes": ["adr-viejo"],
        }
        for i in range(n)
    ]


def _preflight(files: list[str]) -> dict:
    return {
        "files": [
            {"file": f, "callers": 20, "heaviest": [{"name": "f", "cyclo": 9}],
             "symbols": 3}
            for f in files
        ],
        "tests": ["tests/test_x.py"],
        "unmapped": [],
    }


def _service():
    return SimpleNamespace(
        get_session=lambda _sid: {"spring_id": "SPR-1"},
        get_session_plan=lambda _sid: {
            "task": "T-1", "scope": ["a.py"], "next": "verificar",
        },
    )


def test_brief_compone_las_tres_secciones(monkeypatch):
    monkeypatch.setattr(knowledge, "_resolve_anchor",
                        lambda *_: ({"type": "file", "value": "a.py", "resolved": True},
                                    ["a.py"], None))
    monkeypatch.setattr(knowledge, "handle_preflight",
                        lambda _ctx, **_: _preflight(["a.py"]))
    monkeypatch.setattr(CkgReadService, "gather_context",
                        staticmethod(lambda *_a, **_k: Success(_nodes())))
    monkeypatch.setattr(composition, "build_sprint_service", lambda _d: _service())

    out = handle_brief(_Ctx(), _payload={
        "file": "a.py", "session_id": "ses_x",
    })

    assert out["type"] == "brief"
    assert out["decisions"][0]["id"] == "adr-0"
    assert out["decisions"][0]["supersedes"] == ["adr-viejo"]
    assert out["impact"]["callers"] == 20
    assert out["impact"]["tests"] == ["tests/test_x.py"]
    assert out["gates"] == {"sprint": "SPR-1", "task": "T-1",
                            "scope": ["a.py"], "next": "verificar"}
    assert out["missing"] == []
    assert out["degraded"] is False


def test_brief_capa_decisiones_a_cinco(monkeypatch):
    monkeypatch.setattr(knowledge, "_resolve_anchor",
                        lambda *_: ({"type": "text", "value": "q", "resolved": True},
                                    [], None))
    monkeypatch.setattr(knowledge, "handle_preflight",
                        lambda _ctx, **_: _preflight(["a.py"]))
    monkeypatch.setattr(CkgReadService, "gather_context",
                        staticmethod(lambda *_a, **_k: Success(_nodes(9))))

    out = handle_brief(_Ctx(), _payload={"text": "q", "files": ["a.py"]})

    assert len(out["decisions"]) == 5
    assert len(out["missing"]) == 1 and "gates" in out["missing"][0]


def test_brief_declara_la_fuente_caida(monkeypatch):
    monkeypatch.setattr(knowledge, "_resolve_anchor",
                        lambda *_: ({"type": "file", "value": "a.py", "resolved": True},
                                    ["a.py"], None))
    monkeypatch.setattr(knowledge, "handle_preflight",
                        lambda _ctx, **_: _preflight(["a.py"]))

    def boom(*_a, **_k):
        raise RuntimeError("ckg roto")

    monkeypatch.setattr(CkgReadService, "gather_context", staticmethod(boom))

    out = handle_brief(_Ctx(), _payload={"file": "a.py"})

    assert out["degraded"] is True
    assert any("decisions" in m for m in out["missing"])
    # Las otras secciones siguen: no es un 200 con secciones vacías sin marca.
    assert out["impact"]["callers"] == 20


def test_brief_sin_archivos_ni_texto_declara_lo_que_falta():
    out = handle_brief(_Ctx(), _payload={})

    assert out["degraded"] is True
    assert any("decisions" in m for m in out["missing"])
    assert any("impact" in m for m in out["missing"])
    assert any("gates" in m for m in out["missing"])
