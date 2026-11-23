"""test_capture_ledger_bdd.py — F1a/F1b: captura del artefacto de razonamiento.

El agente ya captura evidencia (`capture`); lo que faltaba era un artefacto del
razonamiento (hipótesis, evidencia, descartes) ligado a la sesión y a la tarea.
Sin él, el gate no puede validar la forma del razonamiento y la consolidación
no tiene nada que escribir (F2).

Contrato:
  - `capture(..., ledger=...)` persiste un nodo `task_ledger` en el CKG.
  - Idempotente por (session, task): la segunda llamada no duplica.
  - Un ledger sin `problem` ni `hypotheses` no se guarda.
  - `capture` sin `ledger` sigue funcionando (backward compat).
  - F1b: `build_ledger_skeleton` prellena desde el trace; `ledger="auto"` o
    `prefill=True` usan el skeleton como base y mergean los campos del agente.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
    CkgReadService,
)
from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers import capture as capture_handler


@pytest.fixture()
def project_dir(tmp_path: Path) -> str:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "project.json").write_text(
        json.dumps({"project": {"meta": {"project_id": "ledger-bdd"}}})
    )
    return str(tmp_path)


def _ctx(project_dir: str, task_id: str = "T-1", session_id: str = "S-1"):
    return ExecutionContext.create(chain_dir=project_dir, task_id=task_id, session_id=session_id)


def _ledger() -> dict:
    return {
        "problem": "el agente no usa walker con criterio",
        "options": [{"id": "A", "summary": "espejo"}, {"id": "B", "summary": "gates"}],
        "chosen": "A",
        "rejected_because": {"B": "solo cobra"},
        "invariants_touched": ["FC-INV-03"],
        "hypotheses": [{"claim": "el espejo cambia la conducta", "test": "mirror_acted_on", "status": "open"}],
        "evidence": [{"criterion": "suite verde", "check": "pytest", "ref": "1685 passed", "result": "pass"}],
        "uncertainties": ["capacidad del modelo"],
        "dead_ends": ["gate de hipótesis obligatorio (T2.1)"],
    }


def _trace_event(event_type: str, data: dict | None = None, session_id: str = "S-1"):
    return SimpleNamespace(
        event_type=event_type,
        data=data or {},
        metadata={"session_id": session_id},
        files=[],
        task_id="",
        timestamp="2026-11-22T00:00:00+00:00",
    )


def test_capture_con_ledger_persiste_en_ckg(project_dir):
    out = capture_handler.capture(
        _ctx(project_dir), {"ledger": _ledger(), "task_id": "T-1", "session_id": "S-1"}
    )

    assert out.ok is True
    assert out.data["ledger"]["saved"] is True
    node_id = out.data["ledger"]["node_id"]
    assert node_id

    nodes = CkgReadService.query_all(project_dir)
    ledgers = [n for n in nodes if n.get("type") == "task-ledger"]
    assert len(ledgers) == 1
    assert ledgers[0]["id"] == node_id
    meta = ledgers[0].get("metadata") or {}
    assert meta.get("session_id") == "S-1"
    assert meta.get("task_id") == "T-1"


def test_ledger_usa_el_session_id_del_payload_no_el_de_ctx(project_dir):
    """El path de tool trae `ctx.session_id == "mcp"`; la sesión real viene en
    el payload. Guardar con ctx dejaba el nodo como `task_ledger-mcp-<task>` y
    la consolidación no podía unirlo con la sesión (medido 2026-11-22)."""
    ctx = _ctx(project_dir, task_id="T-9", session_id="mcp")

    out = capture_handler.capture(
        ctx,
        {"ledger": _ledger(), "task_id": "T-9", "session_id": "S-real"},
    )

    assert out.ok is True
    node_id = out.data["ledger"]["node_id"]
    assert "S-real" in node_id
    assert "mcp" not in node_id

    ledgers = [
        n for n in CkgReadService.query_all(project_dir)
        if n.get("type") == "task-ledger"
    ]
    assert len(ledgers) == 1
    assert (ledgers[0].get("metadata") or {}).get("session_id") == "S-real"


def test_capture_sin_ledger_sigue_funcionando(project_dir):
    out = capture_handler.capture(_ctx(project_dir), {"evidence": "ok"})

    assert out.ok is True
    assert out.data["captured"] is True
    assert out.data["ledger"]["saved"] is False


def test_capture_con_ledger_invalido_no_guarda(project_dir):
    out = capture_handler.capture(
        _ctx(project_dir), {"ledger": {"evidence": []}}
    )

    assert out.ok is True
    assert out.data["ledger"]["saved"] is False
    nodes = CkgReadService.query_all(project_dir)
    assert not [n for n in nodes if n.get("type") == "task-ledger"]


def test_capture_con_ledger_es_idempotente(project_dir):
    ctx = _ctx(project_dir)
    first = capture_handler.capture(ctx, {"ledger": _ledger(), "task_id": "T-1", "session_id": "S-1"})
    second = capture_handler.capture(ctx, {"ledger": _ledger(), "task_id": "T-1", "session_id": "S-1"})

    assert first.data["ledger"]["saved"] is True
    assert second.data["ledger"].get("skipped") is True
    nodes = CkgReadService.query_all(project_dir)
    assert len([n for n in nodes if n.get("type") == "task-ledger"]) == 1


def test_build_ledger_skeleton_prefill_desde_trace(project_dir, monkeypatch):
    events = [
        _trace_event("session_state", {"writes": 3, "verified": False, "touched": ["a.py", "b.py"]}),
        _trace_event("flight_control_denied", {"gate": "scope"}),
        _trace_event("flight_control_scope_expanded", {"file": "c.py"}),
        _trace_event("run_tests", {"passed": 10, "total": 12}),
    ]

    class _Trace:
        def load_traces(self, _chain_dir):
            return list(events)

    from fast_mcp.features.event_tracing.application.services import trace_factory
    monkeypatch.setattr(trace_factory, "build_trace_service", lambda _d: _Trace())

    skel = capture_handler.build_ledger_skeleton(project_dir, "S-1", "T-1")

    assert skel["files"] == ["a.py", "b.py"]
    assert skel["denials"] == 1
    assert skel["scope_expanded"] == 1
    assert skel["writes"] == 3
    assert skel["verified"] is False
    assert skel["evidence"] and skel["evidence"][0]["result"] == "fail"
    assert skel["problem"] == ""
    assert skel["hypotheses"] == []
    assert "1 denial(es)" in skel["uncertainties"][0]


def test_build_ledger_skeleton_sin_trace_devuelve_vacio(project_dir, monkeypatch):
    class _Trace:
        def load_traces(self, _chain_dir):
            return []

    from fast_mcp.features.event_tracing.application.services import trace_factory
    monkeypatch.setattr(trace_factory, "build_trace_service", lambda _d: _Trace())

    skel = capture_handler.build_ledger_skeleton(project_dir, "S-1", "T-1")

    assert skel["files"] == []
    assert skel["evidence"] == []
    assert skel["writes"] == 0
    assert skel["verified"] is None
    assert skel["problem"] == ""


def test_capture_con_ledger_auto_usa_el_skeleton(project_dir, monkeypatch):
    events = [
        _trace_event("session_state", {"writes": 1, "verified": True, "touched": ["x.py"]}),
    ]

    class _Trace:
        def load_traces(self, _chain_dir):
            return list(events)

    from fast_mcp.features.event_tracing.application.services import trace_factory
    monkeypatch.setattr(trace_factory, "build_trace_service", lambda _d: _Trace())

    out = capture_handler.capture(
        _ctx(project_dir), {"ledger": "auto", "task_id": "T-1", "session_id": "S-1"}
    )

    assert out.ok is True
    assert out.data["ledger"]["saved"] is True
    assert out.data["skeleton"]["files"] == ["x.py"]
    assert out.data["skeleton"]["verified"] is True

    nodes = CkgReadService.query_all(project_dir)
    ledgers = [n for n in nodes if n.get("type") == "task-ledger"]
    assert len(ledgers) == 1
    import json as _json
    body = _json.loads(ledgers[0]["rationale"])
    assert body["files"] == ["x.py"]
    assert body["problem"] == ""


def test_capture_con_prefill_y_ledger_parcial_mergea(project_dir, monkeypatch):
    events = [
        _trace_event("session_state", {"writes": 2, "verified": False, "touched": ["y.py"]}),
    ]

    class _Trace:
        def load_traces(self, _chain_dir):
            return list(events)

    from fast_mcp.features.event_tracing.application.services import trace_factory
    monkeypatch.setattr(trace_factory, "build_trace_service", lambda _d: _Trace())

    out = capture_handler.capture(
        _ctx(project_dir),
        {"prefill": True, "ledger": {"problem": "metacognicion", "hypotheses": [{"claim": "h1"}]},
         "task_id": "T-1", "session_id": "S-1"},
    )

    assert out.ok is True
    assert out.data["ledger"]["saved"] is True
    nodes = CkgReadService.query_all(project_dir)
    ledgers = [n for n in nodes if n.get("type") == "task-ledger"]
    import json as _json
    body = _json.loads(ledgers[0]["rationale"])
    assert body["problem"] == "metacognicion"
    assert body["hypotheses"] == [{"claim": "h1"}]
    assert body["files"] == ["y.py"]
    assert body["writes"] == 2