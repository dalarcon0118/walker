"""test_skill_token_bdd.py — token de skill por sub-estado (P1.1).

El gate usaba `since=state.updated_at`: cualquier submit cambia `updated_at` y
obligaba a recargar el skill en cada paso (el paso artificial que reporta el
feedback). El token correcto es `(task_id, node, sub_state)`: la carga vale para
la entrada donde se hizo, no para "después del último submit".

Estos tests fijan el contrato del tracer, que es donde vive la decisión.
"""
from __future__ import annotations

from types import SimpleNamespace

from fast_mcp.features.event_tracing.infrastructure.cognitive_tracer import (
    CognitiveTracer,
)


def _ev(skill="review-x", task="T-1", ts="2026-11-17T09:00:00+00:00", node="", sub=""):
    data = {"skill": skill}
    if node or sub:
        data.update({"node": node, "sub_state": sub})
    return SimpleNamespace(event_type="skill_loaded", task_id=task, timestamp=ts, data=data)


def _tracer(events):
    return CognitiveTracer(SimpleNamespace(load_traces=lambda chain: events))


def test_token_de_la_entrada_vale_aunque_haya_submits_despues():
    """La carga del skill se hizo antes de dos submits: sigue valiendo."""
    events = [_ev(ts="2026-11-17T09:00:00+00:00", node="VERIFY", sub="QA")]
    # `state.updated_at` es posterior (hubo submit), pero la entrada es la misma.
    assert _tracer(events).has_skill_load(
        "c", task_id="T-1", skills={"review-x"},
        since="2026-11-17T10:00:00+00:00",
        node="VERIFY", sub_state="QA",
    ) is True


def test_token_de_otra_entrada_no_vale():
    """Un load de otro sub-estado no satisface el gate, aunque sea reciente."""
    events = [_ev(ts="2026-11-17T11:00:00+00:00", node="IMPLEMENT", sub="WORK")]
    assert _tracer(events).has_skill_load(
        "c", task_id="T-1", skills={"review-x"},
        since="2026-11-17T10:00:00+00:00",
        node="VERIFY", sub_state="QA",
    ) is False


def test_token_de_otra_tarea_no_vale():
    events = [_ev(task="T-2", node="VERIFY", sub="QA")]
    assert _tracer(events).has_skill_load(
        "c", task_id="T-1", skills={"review-x"},
        node="VERIFY", sub_state="QA",
    ) is False


def test_evento_legacy_sigue_usando_since():
    """Compat: un load sin posición (cliente viejo) se juzga por timestamp."""
    events = [_ev(ts="2026-11-17T11:00:00+00:00")]
    tracer = _tracer(events)
    assert tracer.has_skill_load(
        "c", task_id="T-1", skills={"review-x"},
        since="2026-11-17T10:00:00+00:00", node="VERIFY", sub_state="QA",
    ) is True
    assert tracer.has_skill_load(
        "c", task_id="T-1", skills={"review-x"},
        since="2026-11-17T12:00:00+00:00", node="VERIFY", sub_state="QA",
    ) is False


def test_sin_token_mantiene_la_regla_vieja():
    """Si el gate no tiene posición, un load reciente sigue valiendo."""
    events = [_ev(ts="2026-11-17T11:00:00+00:00", node="VERIFY", sub="QA")]
    assert _tracer(events).has_skill_load(
        "c", task_id="T-1", skills={"review-x"},
        since="2026-11-17T10:00:00+00:00",
    ) is True
    assert _tracer(events).has_skill_load(
        "c", task_id="T-1", skills={"review-x"},
        since="2026-11-17T12:00:00+00:00",
    ) is False


def test_otro_skill_no_satisface():
    events = [_ev(skill="otro", node="VERIFY", sub="QA")]
    assert _tracer(events).has_skill_load(
        "c", task_id="T-1", skills={"review-x"},
        node="VERIFY", sub_state="QA",
    ) is False
