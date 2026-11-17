"""test_context_anchor_bdd.py — el ancla del contexto: delta → archivo → texto.

La estrategia es anclar en la TAREA (delta), porque es lo que el agente está
trabajando: de la tarea salen los archivos, de los archivos el grafo, y del
grafo el CKG. Pero el delta puede estar vacío, y entonces el contexto tiene que
decaer a archivo o texto SIN fingir que encontró algo: un `anchor` explícito
distingue "no hay nada que saber" de "no encontré el ancla".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fast_mcp.memories.handlers import knowledge


def _ctx(chain_dir="/tmp/proyecto"):
    return SimpleNamespace(chain_dir=chain_dir, project_id="proyecto")


@pytest.fixture()
def spec_presente(monkeypatch):
    monkeypatch.setattr(
        knowledge, "_find_task_spec",
        lambda chain, task: {
            "spec_id": task, "objective": "arreglar X",
            "files": ["pkg/a.py", "pkg/b.py"], "status": "draft",
        },
    )


def test_ancla_en_la_tarea_y_usa_sus_archivos(spec_presente):
    anchor, files, spec = knowledge._resolve_anchor(_ctx(), "SPEC-001", "", "")

    assert anchor["type"] == "task"
    assert anchor["resolved"] is True
    assert files == ["pkg/a.py", "pkg/b.py"]
    assert spec["objective"] == "arreglar X"


def test_si_la_tarea_no_existe_cae_al_archivo_y_lo_dice(monkeypatch):
    monkeypatch.setattr(knowledge, "_find_task_spec", lambda chain, task: None)

    anchor, files, spec = knowledge._resolve_anchor(_ctx(), "SPEC-999", "pkg/a.py", "")

    assert anchor["type"] == "file"
    assert files == [] and spec is None
    assert "delta" in anchor["reason"], "el agente tiene que saber por qué no usó la tarea"


def test_sin_ancla_resuelve_none_y_explica():
    anchor, _, _ = knowledge._resolve_anchor(_ctx(), "", "", "")

    assert anchor == {
        "type": "none", "value": "", "resolved": False,
        "reason": "sin task_id, file ni text: no hay ancla para el contexto",
    }


def test_archivo_gana_al_texto_sin_tarea():
    anchor, _, _ = knowledge._resolve_anchor(_ctx(), "", "pkg/a.py", "promociones")

    assert anchor["type"] == "file"


def test_busca_la_spec_aunque_el_sprint_no_este_activo(monkeypatch):
    """`get_active_spring` da None con sprints en `planning`; la tarea igual existe."""
    class _Svc:
        def get_active_spring(self):
            return None

        def list_springs(self):
            return [{"spring_id": "SPR-1"}, {"spring_id": "SPR-0"}]

        def get_specs(self, spring_id):
            return [{"spec_id": "SPEC-001", "files": ["pkg/a.py"]}] if spring_id == "SPR-1" else []

        def get_spec(self, spring_id, spec_id):
            return None

    import fast_mcp.composition as composition

    monkeypatch.setattr(composition, "build_sprint_service", lambda chain: _Svc())

    found = knowledge._find_task_spec("/tmp/proyecto", "SPEC-001")

    assert found and found["files"] == ["pkg/a.py"]


def test_agent_context_devuelve_el_ancla_siempre(monkeypatch, spec_presente):
    class _Ctx:
        chain_dir = "/tmp/proyecto"
        project_id = "proyecto"

        def code_context_service(self):
            return SimpleNamespace(execute=lambda target_file, query: {"symbols": [], "relations": {}})

    from fast_mcp.features.cognitive_knowledge.application.services import ckg_read
    from fast_mcp.features.event_tracing.application.services import trace_factory
    from returns.result import Success

    monkeypatch.setattr(
        ckg_read.CkgReadService, "gather_context",
        classmethod(lambda cls, chain_dir, query="": Success([])),
    )
    monkeypatch.setattr(trace_factory, "build_trace_service", lambda cd: None)

    out = knowledge.handle_agent_context(_Ctx(), task_id="SPEC-001")

    assert out["anchor"]["type"] == "task"
    assert out["task"]["spec_id"] == "SPEC-001"
    assert out["file"] == "pkg/a.py", "usa el primer archivo declarado por la tarea"
