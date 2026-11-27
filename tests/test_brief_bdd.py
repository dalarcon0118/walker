"""test_brief_bdd.py — `brief` compuesto (AGENT-EFFICIENCY §2.1 / M-01).

Contrato: una llamada devuelve anchor + decisions + impact + gates, y **nunca
presenta una respuesta parcial como completa**: `missing[]` y `degraded` son
obligatorios. Si una fuente cae, el resto sigue y la sección faltante se
declara (escenario 1 de §4).
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
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
            {"file": f, "callers": 20,
             "symbols": [{"name": "f", "cyclo": 9, "line": 10}],
             "called_by": ["g"], "calls": ["h"], "imports": ["os"]}
            for f in files
        ],
        "tests": ["tests/test_x.py"],
        "unmapped": [],
    }


@pytest.fixture(autouse=True)
def _sin_cache_de_impacto():
    """El impacto se cachea por firma de archivo y los tests comparten rutas."""
    knowledge._IMPACT_CACHE.clear()
    yield
    knowledge._IMPACT_CACHE.clear()


def _service():
    plan = {"task": "T-1", "acceptance": ["a"], "scope": ["a.py"],
            "next": "verificar"}
    spec = {"spec_id": "SPEC-001", "task_context": {"task_id": "T-1"},
            "acceptance_criteria": ["a"], "files": ["a.py"],
            "status": "in_progress", "predicates": {}}
    return SimpleNamespace(
        get_session=lambda _sid: {"spring_id": "SPR-1", "flight_plan": plan},
        get_session_plan=lambda _sid: plan,
        get_specs=lambda _spr: [spec],
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
    assert out["gates"]["sprint"] == "SPR-1"
    assert out["gates"]["task"] == "T-1"
    assert out["gates"]["scope"] == ["a.py"]
    assert out["gates"]["scope_total"] == 1
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


def test_brief_resuelve_el_ancla_desde_el_plan_si_la_spec_no_tiene_task_context(monkeypatch):
    """El plan sabe la tarea/alcance aunque la spec no declare `task_context`."""
    knowledge._IMPACT_CACHE.clear()
    monkeypatch.setattr(knowledge, "_resolve_anchor",
                        lambda *_: ({"type": "none", "value": "", "resolved": False,
                                     "reason": "task_id no está en el delta"}, [], None))
    monkeypatch.setattr(knowledge, "handle_preflight",
                        lambda _ctx, **_: _preflight(["a.py"]))
    monkeypatch.setattr(CkgReadService, "gather_context",
                        staticmethod(lambda *_a, **_k: Success(_nodes())))

    plan = {"task": "T-1", "acceptance": ["a"], "scope": ["a.py"], "next": "verificar"}
    spec = {"spec_id": "SPEC-001", "task_context": {}, "acceptance_criteria": ["a"],
            "files": ["a.py"], "status": "in_progress", "predicates": {}}
    monkeypatch.setattr(composition, "build_sprint_service", lambda _d: SimpleNamespace(
        get_session=lambda _sid: {"spring_id": "SPR-1", "flight_plan": plan},
        get_session_plan=lambda _sid: plan,
        get_specs=lambda _spr: [spec],
    ))

    out = handle_brief(_Ctx(), _payload={"session_id": "ses_x", "task_id": "T-1"})

    assert out["anchor"]["type"] == "plan"
    assert out["impact"]["callers"] == 20
    assert not any("sin ancla" in m for m in out["missing"])
    assert not any("impact" in m for m in out["missing"])


def test_impact_se_cachea_por_firma_de_archivo(monkeypatch):
    """Preflight cuesta ~365 ms; el flight card se pide varias veces por tarea."""
    knowledge._IMPACT_CACHE.clear()
    calls: list[int] = []
    monkeypatch.setattr(knowledge, "_resolve_anchor",
                        lambda *_: ({"type": "file", "value": "a.py", "resolved": True},
                                    ["a.py"], None))

    def counting_preflight(_ctx, **_kwargs):
        calls.append(1)
        return _preflight(["a.py"])

    monkeypatch.setattr(knowledge, "handle_preflight", counting_preflight)
    monkeypatch.setattr(CkgReadService, "gather_context",
                        staticmethod(lambda *_a, **_k: Success(_nodes())))
    monkeypatch.setattr(composition, "build_sprint_service", lambda _d: _service())

    first = handle_brief(_Ctx(), _payload={"file": "a.py", "session_id": "ses_x"})
    second = handle_brief(_Ctx(), _payload={"file": "a.py", "session_id": "ses_x"})

    assert first["impact"]["cached"] is False
    assert second["impact"]["cached"] is True
    assert len(calls) == 1, "la segunda llamada no vuelve a pagar preflight"


def test_la_cache_de_impacto_se_invalida_si_el_archivo_cambia(tmp_path):
    class _TmpCtx:
        chain_dir = str(tmp_path)
        project_root = str(tmp_path)

    target = tmp_path / "a.py"
    target.write_text("x", encoding="utf-8")
    before = knowledge._impact_key(_TmpCtx(), ["a.py"])
    os.utime(target, ns=(1, 2_000_000_000))
    after = knowledge._impact_key(_TmpCtx(), ["a.py"])

    assert before != after


def test_brief_declara_el_conflicto_entre_step_y_plan(monkeypatch):
    """No puede decir `START.SELECT` y `spec completed` sin marcar cuál manda."""
    knowledge._IMPACT_CACHE.clear()
    monkeypatch.setattr(knowledge, "_resolve_anchor",
                        lambda *_: ({"type": "file", "value": "a.py", "resolved": True},
                                    ["a.py"], None))
    monkeypatch.setattr(knowledge, "handle_preflight",
                        lambda _ctx, **_: _preflight(["a.py"]))
    monkeypatch.setattr(CkgReadService, "gather_context",
                        staticmethod(lambda *_a, **_k: Success(_nodes())))
    plan = {"task": "T-1", "acceptance": ["a"], "scope": ["a.py"], "next": "verificar"}
    spec = {"spec_id": "SPEC-001", "task_context": {"task_id": "T-1"},
            "acceptance_criteria": ["a"], "files": ["a.py"],
            "status": "completed", "predicates": {}}
    monkeypatch.setattr(composition, "build_sprint_service", lambda _d: SimpleNamespace(
        get_session=lambda _sid: {"spring_id": "SPR-1", "flight_plan": plan},
        get_session_plan=lambda _sid: plan,
        get_specs=lambda _spr: [spec],
    ))
    monkeypatch.setattr(composition, "workflow_tool_runner",
                        lambda _name: (lambda **_kw: {"current_position": "START.SELECT"}))

    out = handle_brief(_Ctx(), _payload={"file": "a.py", "session_id": "ses_x"})

    assert out["consistency"]["ok"] is False
    assert out["consistency"]["governs"] == "plan"
    assert "SPEC-001" in out["consistency"]["reason"]


def _brief_con_preflight(monkeypatch, preflight: dict) -> dict:
    monkeypatch.setattr(knowledge, "_resolve_anchor",
                        lambda *_: ({"type": "file", "value": "a.py", "resolved": True},
                                    ["a.py"], None))
    monkeypatch.setattr(knowledge, "handle_preflight", lambda _ctx, **_: preflight)
    monkeypatch.setattr(CkgReadService, "gather_context",
                        staticmethod(lambda *_a, **_k: Success(_nodes())))
    monkeypatch.setattr(composition, "build_sprint_service", lambda _d: _service())
    return handle_brief(_Ctx(), _payload={"file": "a.py", "session_id": "ses_x"})


def test_brief_compone_el_grafo_del_cpg(monkeypatch):
    """T-CPG-ADOPTION: el impacto no es solo conteos, es estructura consultable.

    Sin esto el agente paga `read` para descubrir qué símbolos y relaciones
    tiene el archivo que `preflight` ya resolvió.
    """
    out = _brief_con_preflight(monkeypatch, _preflight(["a.py"]))

    entry = out["impact"]["files"][0]
    assert entry["symbols"] == [{"name": "f", "cyclo": 9, "line": 10}]
    assert entry["called_by"] == ["g"]
    assert entry["calls"] == ["h"]
    assert entry["imports"] == ["os"]
    assert out["degraded"] is False


def test_brief_declara_el_cpg_mudo(monkeypatch):
    """Un archivo sin respuesta del CPG no puede salir con relaciones vacías."""
    preflight = {
        "files": [{"file": "a.py", "error": "CPGError: índice ilegible"}],
        "tests": [],
        "unmapped": [],
    }

    out = _brief_con_preflight(monkeypatch, preflight)

    assert out["degraded"] is True
    assert any("CPG sin respuesta" in m for m in out["missing"])
    # La sección sigue presente: se declara el hueco, no se borra.
    assert out["impact"]["files"][0]["file"] == "a.py"
