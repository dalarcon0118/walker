"""Tests BDD (pytest-bdd) — Event tracing design (ADR-WALKER-018).

Feature value: garantizar integridad y dedupe central en el
bounded context Trace.

Escenarios: bdd/features/event_tracing_design.feature
"""
from __future__ import annotations

import gzip
import json
import os
import tempfile

import pytest
from pytest_bdd import given, scenarios, then, when

from features.event_tracing.domain.models import TraceEvent

scenarios("bdd/features/event_tracing_design.feature")


@pytest.fixture
def etd_ctx() -> dict:
    return {}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_trace_index(chain_dir: str, docs: list[dict]) -> str:
    """Escribe el indice trace canonico (trace.json.gz) en <root>/data/.

    Formato CognitiveGraph: {"index": "trace", "docs": [...], "edges": []}.
    """
    data_dir = os.path.join(chain_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "trace.json.gz")
    payload = {"index": "trace", "docs": docs, "edges": []}
    with open(path, "wb") as f:
        f.write(gzip.compress(json.dumps(payload).encode()))
    return path


# ── Roundtrip ────────────────────────────────────────────────────────────────


@given("un TraceEvent con id, sprint_id, parent_id, parent_type y data")
def _trace_event_with_identity(etd_ctx: dict):
    etd_ctx["ev"] = TraceEvent(
        id="ev-123",
        event_type="TRANSITION",
        source="walker_task",
        agent_id="a1",
        session_id="s1",
        sprint_id="S-1",
        task_id="TASK-001",
        node="IMPLEMENT",
        tool="bash",
        input={"cmd": "tsc"},
        output={"code": 0},
        files=["src/a.ts"],
        parent_type="sprint",
        parent_id="S-1",
        timestamp="2026-08-02T10:00:00+00:00",
        started_at="2026-08-02T10:00:00+00:00",
        finished_at="2026-08-02T10:00:01+00:00",
        duration_ms=1000,
        metadata={"k": "v"},
        data={"gate": "start_task"},
    )


@when("se convierte a dict y se restaura desde dict")
def _roundtrip(etd_ctx: dict):
    ev = etd_ctx["ev"]
    etd_ctx["restored"] = TraceEvent.from_dict(ev.to_dict())


@then("el evento restaurado mantiene id, sprint_id, parent_id y parent_type")
def _assert_identity_preserved(etd_ctx: dict):
    restored = etd_ctx["restored"]
    assert restored.id == "ev-123"
    assert restored.sprint_id == "S-1"
    assert restored.parent_id == "S-1"
    assert restored.parent_type == "sprint"


@then("event_type y data se preservan intactos")
def _assert_event_type_data(etd_ctx: dict):
    restored = etd_ctx["restored"]
    assert restored.event_type == "TRANSITION"
    assert restored.data == {"gate": "start_task"}


@then("el timestamp se mantiene igual")
def _assert_timestamp_preserved(etd_ctx: dict):
    restored = etd_ctx["restored"]
    assert restored.timestamp == etd_ctx["ev"].timestamp


# ── Search ───────────────────────────────────────────────────────────────────


@given("una lista de eventos de tracing")
def _list_of_events(etd_ctx: dict):
    etd_ctx["events"] = [
        TraceEvent(event_type="tool_call", tool="Write", task_id="T-1"),
        TraceEvent(event_type="agent_message", task_id="T-2"),
    ]


@when("se busca con un filtro desconocido")
def _search_unknown_filter(etd_ctx: dict):
    from features.event_tracing.application.search_service import search_traces

    etd_ctx["results"] = search_traces(
        etd_ctx["events"], {"unknown_key": "x"}
    )


@then("no se devuelven eventos (fail-closed)")
def _assert_fail_closed(etd_ctx: dict):
    assert etd_ctx["results"] == []


@given("eventos con sprint_id S-1 y S-2")
def _events_with_sprint_ids(etd_ctx: dict):
    etd_ctx["events"] = [
        TraceEvent(event_type="tool_call", sprint_id="S-1"),
        TraceEvent(event_type="tool_call", sprint_id="S-2"),
    ]


@when("se filtra por sprint_id S-1")
def _filter_by_sprint_id(etd_ctx: dict):
    from features.event_tracing.application.search_service import search_traces

    etd_ctx["results"] = search_traces(etd_ctx["events"], {"sprint_id": "S-1"})


@then("solo el evento S-1 se devuelve")
def _assert_sprint_id_filtered(etd_ctx: dict):
    results = etd_ctx["results"]
    assert len(results) == 1 and results[0]["sprint_id"] == "S-1"


@when("se aplica el filtro alternativo sprint S-2")
def _filter_by_sprint(etd_ctx: dict):
    from features.event_tracing.application.search_service import search_traces

    etd_ctx["results2"] = search_traces(etd_ctx["events"], {"sprint": "S-2"})


@then("el filtro alternativo sprint S-2 también funciona")
def _assert_sprint_filter(etd_ctx: dict):
    results = etd_ctx["results2"]
    assert len(results) == 1 and results[0]["sprint_id"] == "S-2"


# ── Dedupe ───────────────────────────────────────────────────────────────────


@given("un índice trace canónico con 3 eventos idénticos")
def _trace_index_with_duplicates(etd_ctx: dict):
    etd_ctx["tmp_dir"] = tempfile.mkdtemp()
    chain_dir = os.path.join(etd_ctx["tmp_dir"], "proj")
    os.makedirs(chain_dir, exist_ok=True)
    dup = {
        "_id": "ev-1",
        "event_type": "tool_call",
        "tool": "bash",
        "task_id": "T-1",
        "timestamp": "2026-08-02T10:00:00+00:00",
        "data": {"cmd": "ls"},
    }
    # 3 docs con el mismo contenido (ids distintos): el dedupe central
    # por firma debe colapsarlos a 1 (contrato del puerto, ADR-018).
    _write_trace_index(chain_dir, [dup, {**dup, "_id": "ev-2"}, {**dup, "_id": "ev-3"}])
    etd_ctx["chain_dir"] = chain_dir


@when("se cargan los traces")
def _load_traces(etd_ctx: dict):
    from features.event_tracing.infrastructure.repository import load_traces

    etd_ctx["events"] = load_traces(etd_ctx["chain_dir"])


@then("solo queda 1 evento")
def _assert_deduped(etd_ctx: dict):
    assert len(etd_ctx["events"]) == 1, f"debería dedupe 3->1: {len(etd_ctx['events'])}"


@then("el task_id se preserva")
def _assert_task_id_preserved(etd_ctx: dict):
    assert etd_ctx["events"][0].task_id == "T-1"


# ── Push ─────────────────────────────────────────────────────────────────────


@given("un directorio de traces vacío")
def _empty_trace_dir(etd_ctx: dict):
    etd_ctx["tmp_dir"] = tempfile.mkdtemp()
    chain_dir = os.path.join(etd_ctx["tmp_dir"], "proj", "data", "spring", "s")
    os.makedirs(chain_dir, exist_ok=True)
    etd_ctx["chain_dir"] = chain_dir


@when("se pushea un evento con task_id T-9")
def _push_event(etd_ctx: dict):
    from features.event_tracing.infrastructure.repository import push_event

    etd_ctx["result"] = push_event(
        etd_ctx["chain_dir"],
        {"event_type": "agent_message", "task_id": "T-9", "input": {"m": "hi"}},
    )


@when("se cargan los traces")
def _load_traces_after_push(etd_ctx: dict):
    from features.event_tracing.infrastructure.repository import load_traces

    etd_ctx["events"] = load_traces(etd_ctx["chain_dir"])


@then("el evento T-9 se recupera correctamente")
def _assert_event_recovered(etd_ctx: dict):
    assert any(e.task_id == "T-9" for e in etd_ctx["events"]), "evento push no leído"


@then("el resultado del push es exitoso")
def _assert_push_ok(etd_ctx: dict):
    assert etd_ctx["result"].get("ok") is True, etd_ctx["result"]
