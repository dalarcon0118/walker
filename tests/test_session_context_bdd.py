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


# ── El CKG descartaba nodos sin `label` ─────────────────────────────────


def test_save_nodes_deriva_label_y_no_descarta_en_silencio(monkeypatch):
    """Sin `label` el storage descartaba el nodo y devolvía nodes_created: 0."""
    from fast_mcp.features.cognitive_knowledge.application.services import ckg_write

    class _Ckg:
        def save_nodes(self, nodes):
            return {"nodes_created": len(nodes), "node_ids": [n.get("id") for n in nodes]}

    monkeypatch.setattr(ckg_write, "_resolve_ckg", lambda chain: (_Ckg(), None))

    result = ckg_write.CkgWriteService.save_nodes(
        "/tmp/x", [{"id": "n1", "statement": "algo pasó", "type": "session"}],
        task_id="T", session_id="ses-1",
    ).value_or({})

    assert result["nodes_saved"] == 1


def test_save_nodes_reporta_los_que_no_puede_guardar(monkeypatch):
    from fast_mcp.features.cognitive_knowledge.application.services import ckg_write

    class _Ckg:
        def save_nodes(self, nodes):
            return {"nodes_created": 0, "node_ids": []}

    monkeypatch.setattr(ckg_write, "_resolve_ckg", lambda chain: (_Ckg(), None))

    result = ckg_write.CkgWriteService.save_nodes("/tmp/x", [{"type": "session"}]).value_or({})

    assert result["nodes_saved"] == 0
    assert result["skipped"] == ["?"], "un descarte silencioso es peor que un error"


# ── capture_knowledge: el cierre automático de sesión ───────────────────


def test_capture_knowledge_escribe_el_hecho_de_la_sesion(monkeypatch):
    from types import SimpleNamespace

    from fast_mcp.features.tasks.handlers import knowledge as task_knowledge

    captured = {}

    def _writer(chain, nodes, task_id, session_id):
        captured.update({"nodes": nodes, "task_id": task_id, "session_id": session_id})
        return {"nodes_saved": 1}

    monkeypatch.setattr(task_knowledge._di, "_KNOWLEDGE_WRITER", _writer)
    monkeypatch.setattr(
        task_knowledge._di, "current_spec",
        lambda chain, task_id="", prefer_status=("in_progress",), exact=False: {
            "spec_id": "SPEC-1", "objective": "arreglar X", "status": "in_progress",
            "files": ["a.py"], "task_context": {"task_id": "T-1", "session_id": "ses-1"},
        },
    )
    monkeypatch.setattr(
        task_knowledge._di, "session_events",
        lambda chain, sid: [SimpleNamespace(event_type="task_autodeclared", files=["b.py"],
                                            metadata={"session_id": sid})],
    )

    out = task_knowledge.capture_knowledge(
        SimpleNamespace(chain_dir="/tmp/x", task_id="", session_id="ses-1"),
        {"session_id": "ses-1", "task_id": "T-1"},
    )

    assert out.ok
    fact = captured["nodes"][0]
    assert "arreglar X" in fact["statement"]
    assert set(fact["metadata"]["files"]) == {"a.py", "b.py"}
    assert captured["session_id"] == "ses-1"


def test_capture_knowledge_sin_nada_que_documentar_falla(monkeypatch):
    from types import SimpleNamespace

    from fast_mcp.features.tasks.handlers import knowledge as task_knowledge

    monkeypatch.setattr(task_knowledge._di, "current_spec", lambda *a, **k: None)
    monkeypatch.setattr(task_knowledge._di, "session_events", lambda *a, **k: [])

    out = task_knowledge.capture_knowledge(
        SimpleNamespace(chain_dir="/tmp/x", task_id="", session_id=""), {}
    )

    assert not out.ok


# ── Escrituras del agente: ni perdidas ni invisibles al join ────────────


def test_save_nodes_guarda_session_id_en_metadata(monkeypatch):
    """El join filtra por metadata.session_id; con solo `source` no aparecía."""
    from fast_mcp.features.cognitive_knowledge.application.services import ckg_write

    captured = {}

    class _Ckg:
        def save_nodes(self, nodes):
            captured["nodes"] = nodes
            return {"nodes_created": len(nodes), "node_ids": [n.get("id") for n in nodes]}

    monkeypatch.setattr(ckg_write, "_resolve_ckg", lambda chain: (_Ckg(), None))

    ckg_write.CkgWriteService.save_nodes(
        "/tmp/x", [{"id": "d1", "statement": "algo"}], task_id="T-1", session_id="ses-7"
    )

    meta = captured["nodes"][0]["metadata"]
    assert meta["session_id"] == "ses-7"
    assert meta["source"] == "session:ses-7"


def test_query_all_normaliza_id_y_label(monkeypatch):
    """El storage devuelve `_id` y sin `label`: `node["id"]` era None."""
    from fast_mcp.features.cognitive_knowledge.application.services import ckg_read

    class _Ckg:
        def query(self, q):
            return [{"_id": "d1", "statement": "una decisión", "type": "decision"}]

    monkeypatch.setattr(ckg_read, "_resolve_ckg", lambda chain: (_Ckg(), None))

    node = ckg_read.CkgReadService.query_all("/tmp/x")[0]

    assert node["id"] == "d1"
    assert node["label"] == "una decisión"


def test_una_decision_del_agente_aparece_en_su_sesion(monkeypatch):
    from fast_mcp.features.cognitive_knowledge.application.services import ckg_read

    class _Ckg:
        def query(self, q):
            return [
                {"_id": "d1", "statement": "decisión de la sesión",
                 "metadata": {"session_id": "ses-1"}},
                {"_id": "d2", "statement": "de otra sesión",
                 "metadata": {"session_id": "ses-2"}},
            ]

    monkeypatch.setattr(ckg_read, "_resolve_ckg", lambda chain: (_Ckg(), None))

    nodes = [
        n for n in ckg_read.CkgReadService.query_all("/tmp/x")
        if (n.get("metadata") or {}).get("session_id") == "ses-1"
    ]

    assert [n["id"] for n in nodes] == ["d1"]


# ── Preflight: qué se toca, a quién afecta y qué lo verifica ────────────


def test_preflight_devuelve_impacto_tests_y_baseline(monkeypatch):
    from fast_mcp.memories.handlers import knowledge as k

    class _Ctx:
        chain_dir = "/tmp/proyecto"

        def code_context_service(self):
            return SimpleNamespace(execute=lambda target_file, query: {
                "symbols": [{"name": "f", "cyclo": 9}, {"name": "g", "cyclo": 2}],
                "relations": {"calls": [{"name": "x"}], "called_by": [{"name": "user"}],
                              "called_by_total": 26, "calls_total": 42, "imports": [1]},
            })

    monkeypatch.setattr(k, "tests_for_files", lambda files, root: ["tests/test_f.py"], raising=False)
    # tests_for_files se importa dentro del handler desde spec_derive
    from fast_mcp.features.tasks.handlers import spec_derive
    monkeypatch.setattr(spec_derive, "tests_for_files", lambda files, root: ["tests/test_f.py"])

    from fast_mcp.features.code_analysis.application import baseline_service
    class _BS:
        def __init__(self, chain): pass
        def capture(self, scope="", light=True):
            return {"structure": {"nodes": 10}, "complexity": {"avg": 3.0},
                    "dimensions": {"structure": "ok"}}
    monkeypatch.setattr(baseline_service, "BaselineService", _BS)

    from fast_mcp.features.cognitive_knowledge.application.services import ckg_read
    from returns.result import Success
    monkeypatch.setattr(ckg_read.CkgReadService, "gather_context",
                        classmethod(lambda cls, chain, query="": Success([])))

    out = k.handle_preflight(_Ctx(), files=["pkg/a.py"])

    assert out["answerable"] is True
    assert out["files"][0]["callers"] == 26
    assert out["files"][0]["heaviest"][0]["name"] == "f"
    assert out["tests"] == ["tests/test_f.py"]
    assert out["baseline"]["structure"] == {"nodes": 10}


def test_preflight_sin_archivos_falla_claro():
    from fast_mcp.memories.handlers import knowledge as k

    out = k.handle_preflight(SimpleNamespace(chain_dir="/tmp/x"), files=[])

    assert "files requerido" in out["error"]
