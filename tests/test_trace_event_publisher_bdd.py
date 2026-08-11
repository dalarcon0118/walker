"""tests/test_trace_event_publisher_bdd.py — EventBus drops no silenciados.

SPEC-002: EventBus.publish y trace_event_publisher no descartan eventos
con except: pass; registran fallos como logs visibles.
"""
from __future__ import annotations

import logging

import pytest

from features.code_analysis.domain.events import EventBus, DomainEvent


class MyTestEvent(DomainEvent):
    source: str = "test"


class FailingListener:
    def __init__(self) -> None:
        self.called = False

    def __call__(self, event: DomainEvent) -> None:
        self.called = True
        raise RuntimeError("listener boom")


def test_fallo_de_publicacion_queda_registrado(caplog):
    bus = EventBus()
    listener = FailingListener()
    bus.subscribe(listener)
    event = MyTestEvent()

    with caplog.at_level(logging.ERROR):
        bus.publish(event)

    assert listener.called
    assert "EventBus: listener dropped event" in caplog.text
    assert "listener boom" in caplog.text


def test_evento_exitoso_se_entrega_al_listener():
    bus = EventBus()
    delivered = []

    def ok_listener(event: DomainEvent) -> None:
        delivered.append(type(event).__name__)

    bus.subscribe(ok_listener)
    event = MyTestEvent()
    bus.publish(event)

    assert delivered == ["MyTestEvent"]
