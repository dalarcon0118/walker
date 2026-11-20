"""test_task_degradations_bdd.py — la degradación que el agente no lee (P1.19).

Un handler puede devolver `ok: True` y dejar parte del trabajo sin hacer (CPG
no disponible, contexto de sprint falló, archivo que no se pudo revisar). Esas
señales vivían sólo en el `data` del resultado: si el agente no lo leía, el
motor "hacía" algo que no hizo. Ahora se extraen con un contrato acotado y la
composición las traza.
"""
from __future__ import annotations

from types import SimpleNamespace

from fast_mcp.features.tasks.domain.degradations import MAX_NOTES, result_notes


def _result(**data):
    return SimpleNamespace(ok=True, data=data, error="")


def test_warnings_del_handler_son_notas():
    notes = result_notes(_result(warnings=["spring_context: boom", "otra"]))

    assert [n["kind"] for n in notes] == ["warning", "warning"]
    assert notes[0]["detail"] == "spring_context: boom"


def test_available_false_anidado_es_su_propia_fuente():
    """`review_code` anida el estado del CPG en `data.cpg`."""
    notes = result_notes(
        _result(cpg={"available": False, "error": "cpg_adapter no inyectado"})
    )

    assert notes == [
        {
            "kind": "unavailable",
            "detail": "cpg_adapter no inyectado",
            "source": "cpg",
        }
    ]


def test_findings_con_error_se_reportan_con_su_archivo():
    notes = result_notes(
        _result(
            findings=[
                {"file": "x.js", "error": "node no disponible"},
                {"file": "y.py", "message": "estilo"},  # no es degradación
            ]
        )
    )

    assert len(notes) == 1
    assert notes[0]["kind"] == "finding_error"
    assert notes[0]["source"] == "x.js"
    assert "node no disponible" in notes[0]["detail"]


def test_resultado_limpio_no_produce_ruido():
    assert result_notes(_result(passed=True, total=9)) == []
    assert result_notes(SimpleNamespace(ok=True, data=None, error="")) == []
    assert result_notes(None) == []


def test_el_techo_evita_que_una_degradacion_repetida_inunde_el_trace():
    notes = result_notes(_result(warnings=[f"w{i}" for i in range(20)]))

    assert len(notes) == MAX_NOTES


def test_la_composicion_traza_las_notas(monkeypatch):
    from fast_mcp import composition
    from fast_mcp.features.tasks.handlers import _di

    events: list[dict] = []
    monkeypatch.setattr(_di, "push_trace", lambda chain, ev: events.append(ev) or True)

    ctx = SimpleNamespace(chain_dir="/proyecto", session_id="S-1", task_id="T-1")
    composition._trace_task_notes(
        "review_code",
        ctx,
        _result(cpg={"available": False, "error": "no inyectado"}),
    )

    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "task_degraded"
    assert event["session_id"] == "S-1" and event["task_id"] == "T-1"
    assert event["data"] == {
        "task": "review_code",
        "kind": "unavailable",
        "detail": "no inyectado",
        "source": "cpg",
    }


def test_el_registry_traza_la_degradacion_de_un_task_real(tmp_path):
    """De punta a punta: TaskRegistry → on_task_done → TraceGraph.

    `review_code` sin `cpg_adapter` devuelve ok=True con `cpg.available=False`.
    Antes eso sólo existía en el data del resultado; ahora queda en el trace.
    """
    import json

    from fast_mcp.composition import _build_task_registry
    from fast_mcp.features.event_tracing.application.services.trace_factory import (
        build_trace_service,
    )
    from fast_mcp.features.tasks.domain.task_registry import ExecutionContext

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "project.json").write_text(
        json.dumps(
            {
                "project": {
                    "meta": {"project_id": "probe"},
                    "location": {"repo_path": str(tmp_path)},
                }
            }
        )
    )
    (tmp_path / "nope.py").write_text("x = 1\n")

    registry = _build_task_registry()
    ctx = ExecutionContext.create(
        chain_dir=str(tmp_path), session_id="S-1", task_id="T-1"
    )

    out = registry.execute("review_code", ctx, {"files": ["nope.py"]})

    assert out.ok is True
    assert out.data["cpg"]["available"] is False

    events = [
        e
        for e in build_trace_service(str(tmp_path)).load_traces(str(tmp_path))
        if e.event_type == "task_degraded"
    ]
    assert len(events) == 1
    event = events[0]
    assert event.data["task"] == "review_code"
    assert event.data["kind"] == "unavailable"
    assert event.data["source"] == "cpg"
    assert event.task_id == "T-1"
    assert (event.metadata or {}).get("session_id") == "S-1"
