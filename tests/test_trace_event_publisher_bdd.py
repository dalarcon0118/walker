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


def test_trace_push_es_invocable_por_el_motor_interno_y_bloqueado_desde_mcp():
    """SPEC-005 #5: las tasks scope=system solo se ejecutan por el motor interno.

    Regresión (capa_rout.md D2): el gate de visibilidad de execute_public_task
    bloqueaba trace_push para TODOS los channels, y trace_event_publisher
    (que descarta el resultado del dispatch) perdía los eventos CPG→trace de
    SPEC-002 G-W1 en silencio.
    """
    from features.engine.application.task_dispatcher import execute_public_task

    # Entrada externa (MCP/CLI): bloqueada.
    blocked = execute_public_task(
        "trace_push", "x", "", {"_payload": {}}, session_id="mcp", channel="mcp"
    )
    assert blocked.get("blocked") is True

    # Motor interno (channel='system'): NO bloqueada.
    out = execute_public_task(
        "trace_push", "x", "", {"_payload": {}}, session_id="", channel="system"
    )
    assert out.get("blocked") is not True


def test_evento_cpg_publicado_llega_al_indice_trace(tmp_path):
    """E2E: un evento CPG publicado via trace_event_publisher llega al índice trace.

    Cubre el camino real roto por la regresión: EventBus → _publish →
    execute_public_task(channel='system') → trace_push → índice trace.
    """
    from tests.conftest import make_chain_dir

    from features.code_analysis.domain.events import DomainEvent, EventBus
    from features.code_analysis.infrastructure.adapters.trace_event_publisher import (
        create_trace_publisher,
    )
    from features.engine.bootstrap import get_module_service
    from features.project import ProjectService

    chain_dir = make_chain_dir(tmp_path, project_id="walker")
    listener = create_trace_publisher(chain_dir)
    assert listener is not None

    class CpgEvent(DomainEvent):
        pass

    bus = EventBus()
    bus.subscribe(listener)
    bus.publish(CpgEvent())

    paths = ProjectService.resolve_paths(chain_dir)
    cg = get_module_service("cognitive_graph")(paths.project_root)
    result = cg.search("trace", {"event_type": "CpgEvent"})
    hits = result.get("hits", {})
    if isinstance(hits, dict):
        entries = hits.get("hits", [])
    else:
        entries = hits
    assert entries, f"no se persistió el evento en trace: {result}"
    assert entries[0]["_source"].get("event_type") == "CpgEvent"
