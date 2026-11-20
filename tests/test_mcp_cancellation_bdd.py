"""AE-INV-09 — una tool cancelada por el cliente no tumba el server MCP.

Escenario real (2026-11-20): `run_tests` tardó 37 s, el cliente canceló a los
30 s, el handler respondió igual y `RequestResponder.respond` falló con
`AssertionError: Request already responded to`. La excepción salió del task
group y el proceso terminó con code 1.

Estos tests fijan el contrato del guard:
1. aplicarlo es idempotente;
2. una respuesta tardía tras cancelación se descarta (no lanza);
3. el cliente recibe exactamente UNA respuesta (la de cancelación).
"""

from __future__ import annotations

import anyio
import pytest
from mcp.shared.session import RequestResponder


class _FakeSession:
    def __init__(self) -> None:
        self.responses: list[tuple[object, object]] = []

    async def _send_response(self, request_id, response) -> None:  # noqa: ANN001
        self.responses.append((request_id, response))


def _install() -> None:
    from fast_mcp.shared_kernel.runtime_safety import install_cancellation_guard

    install_cancellation_guard()


def test_install_es_idempotente():
    from fast_mcp.shared_kernel.runtime_safety import install_cancellation_guard

    first = install_cancellation_guard()
    second = install_cancellation_guard()
    assert first in (True, False)
    assert second is False
    assert getattr(RequestResponder.respond, "_walker_cancellation_safe", False) is True


def test_respuesta_tardia_tras_cancelacion_no_lanza():
    _install()

    async def scenario() -> _FakeSession:
        session = _FakeSession()
        responder = RequestResponder(
            request_id="req-1",
            request_meta=None,
            request=object(),
            session=session,
            on_complete=lambda _r: None,
        )
        responder.__enter__()
        try:
            await responder.cancel()
            # El handler "termina" después de la cancelación: el guard descarta.
            await responder.respond({"result": "tardío"})
        finally:
            try:
                responder.__exit__(None, None, None)
            except BaseException:  # noqa: BLE001 - el scope cancelado puede re-lanzar
                pass
        return session

    session = anyio.run(scenario)
    assert len(session.responses) == 1, "sólo la respuesta de cancelación"
    assert session.responses[0][0] == "req-1"


def test_respuesta_normal_sigue_funcionando():
    _install()

    async def scenario() -> _FakeSession:
        session = _FakeSession()
        responder = RequestResponder(
            request_id="req-2",
            request_meta=None,
            request=object(),
            session=session,
            on_complete=lambda _r: None,
        )
        responder.__enter__()
        try:
            await responder.respond({"ok": True})
        finally:
            try:
                responder.__exit__(None, None, None)
            except BaseException:  # noqa: BLE001
                pass
        return session

    session = anyio.run(scenario)
    assert len(session.responses) == 1
    assert session.responses[0][0] == "req-2"


def test_build_server_instala_guard_y_metricas():
    from fast_mcp.server import build_server

    server = build_server()
    assert getattr(server._tool_manager.call_tool, "_walker_metrics", False) is True
    assert getattr(RequestResponder.respond, "_walker_cancellation_safe", False) is True
