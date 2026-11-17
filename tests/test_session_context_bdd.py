"""test_session_context_bdd.py — la sesión como clave de join entre grafos.

El diseño: `session_id` une TRACE (qué hizo el agente), DELTA (qué tareas), CKG
(decisiones y hechos) y CPG (los archivos tocados). Cada capa guarda el id en un
lugar distinto —metadata, task_context, metadata.source— y esta consulta las
junta, declarando la que quedó vacía.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fast_mcp.memories.handlers import knowledge


def _ctx():
    class _Ctx:
        chain_dir = "/tmp/proyecto"
        project_id = "proyecto"

        def code_context_service(self):
            return SimpleNamespace(
                execute=lambda target_file, query: {
                    "symbols": [{"name": "f"}],
                    "relations": {"calls": [], "called_by": [], "called_by_total": 7},
                }
            )

    return _Ctx()


@pytest.fixture()
def servicios(monkeypatch):
    events = [
        SimpleNamespace(event_type="task_autodeclared", timestamp="2026-01-01T00:00:00",
                        task_id="T-1", files=["b.py"], data={},
                        metadata={"session_id": "ses-1"}),
        SimpleNamespace(event_type="otra_cosa", timestamp="2026-01-01T00:01:00",
                        task_id="", files=["c.py"], data={},
                        metadata={"session_id": "ses-2"}),
    ]
    trace_factory = __import__(
        "fast_mcp.features.event_tracing.application.services.trace_factory",
        fromlist=["x"],
    )
    monkeypatch.setattr(
        trace_factory, "build_trace_service",
        lambda cd: SimpleNamespace(load_traces=lambda c: events),
    )

    class _Svc:
        def get_active_spring(self):
            return None

        def list_springs(self):
            return [{"spring_id": "SPR-1"}]

        def get_specs(self, sid):
            return [
                {"spec_id": "SPEC-1", "objective": "x", "status": "in_progress",
                 "files": ["d.py"], "task_context": {"session_id": "ses-1"}},
                {"spec_id": "SPEC-2", "objective": "y", "status": "in_progress",
                 "files": ["e.py"], "task_context": {"session_id": "ses-2"}},
            ]

    import fast_mcp.composition as composition

    monkeypatch.setattr(composition, "build_sprint_service", lambda cd: _Svc())

    from fast_mcp.features.cognitive_knowledge.application.services import ckg_read
    from returns.result import Success

    monkeypatch.setattr(
        ckg_read.CkgReadService, "gather_context",
        classmethod(lambda cls, chain_dir, query="": Success([])),
    )


def test_une_trace_y_delta_por_session(servicios):
    out = knowledge.handle_session_context(_ctx(), session_id="ses-1")

    assert out["trace"]["count"] == 1
    assert out["trace"]["types"] == ["task_autodeclared"]
    assert [t["spec_id"] for t in out["tasks"]] == ["SPEC-1"]
    assert set(out["files"]) == {"b.py", "d.py"}


def test_no_mezcla_otra_sesion(servicios):
    out = knowledge.handle_session_context(_ctx(), session_id="ses-2")

    assert [t["spec_id"] for t in out["tasks"]] == ["SPEC-2"]
    assert out["files"] == ["c.py", "e.py"]


def test_declara_las_capas_vacias(servicios):
    out = knowledge.handle_session_context(_ctx(), session_id="ses-1")

    assert "ckg" in out["empty_layers"], "sin hechos documentados hay que decirlo"
    assert "delta" not in out["empty_layers"]


def test_cpg_resume_los_archivos_tocados(servicios):
    out = knowledge.handle_session_context(_ctx(), session_id="ses-1")

    assert out["cpg"] and out["cpg"][0]["called_by"] == 7


def test_sin_session_id_falla_claro():
    out = knowledge.handle_session_context(_ctx(), session_id="")

    assert out["error"] == "session_id requerido"


def test_documenting_knowledge_propaga_la_sesion(monkeypatch):
    """Una decisión sin session_id no se puede unir después."""
    from fast_mcp.features.cognitive_knowledge.application.services import ckg_write
    from returns.result import Success

    captured = {}

    def _save(chain_dir, nodes, task_id="", session_id=""):
        captured.update({"nodes": nodes, "session_id": session_id, "task_id": task_id})
        return Success({"saved": 1})

    monkeypatch.setattr(ckg_write.CkgWriteService, "save_nodes", staticmethod(_save))

    knowledge.handle_documenting_knowledge(
        _ctx(), type="decision", statement="usar X", session_id="ses-9", task_id="T-9"
    )

    assert captured["session_id"] == "ses-9"
    assert captured["task_id"] == "T-9"
