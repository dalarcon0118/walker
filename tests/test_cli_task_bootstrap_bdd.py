"""tests/test_cli_task_bootstrap_bdd.py — Regresión del bootstrap en handle_task.

ANTES: handle_task (dispatcher CLI de `walker <project>/<task>`) llamaba a
execute_public_task SIN construir el composition root. Los demás handlers
(status/submit/transition) sí hacían WalkerApp(AppContainer.build()). Como
el service locator quedaba vacío, resolve_service("task_registry") devolvía
None y execute_public_task crasheaba con:
    'NoneType' object has no attribute 'get_task_info'
El CLI `walker react-native-frontend/build_code_index` fallaba por esto.

POR QUÉ NO LO DETECTABAN LOS TESTS: tests/conftest.py tiene un fixture
autouse (_populate_service_locator) que ejecuta AppContainer.build() ANTES de
cada test. Así el locator siempre llega poblado y el escenario real
(proceso fresco donde handle_task es el primer punto de contacto) nunca se
ejercitaba.

ESTE TEST reproduce el escenario fresco: resetea el locator y verifica que
handle_task lo reconstruye por sí mismo (y no crashea con get_task_info).
"""
from __future__ import annotations

from protocols.cli.cli_interface.infrastructure.contracts import Request
from protocols.cli.cli_interface.application import handlers as cli_handlers


def test_handle_task_self_bootstraps_composition_root(monkeypatch, mcp_project):
    """Sin bootstrap previo, handle_task debe poblar el service locator."""
    from shared.core.engine.bootstrap import reset_module_registry

    # Simula un proceso CLI fresco: locator vacío.
    reset_module_registry()

    # Aisla la resolución de chain_dir (IO/git) — no es lo que testeamos aquí.
    monkeypatch.setattr(cli_handlers, "_ensure_chain_dir", lambda *a, **k: "")

    from app.container import AppContainer

    calls: list[int] = []
    orig_build = AppContainer.build

    def _wrapped_build():
        calls.append(1)
        return orig_build()

    monkeypatch.setattr(AppContainer, "build", _wrapped_build)

    req = Request(
        method="POST",
        path="walker/build_code_index",
        params={"projectId": "walker", "taskName": "build_code_index"},
        flags={},
    )
    resp = cli_handlers.handle_task(req)

    # 1. handle_task debe haber construido el composition root.
    assert calls, (
        "handle_task debe llamar AppContainer.build() para poblar el service "
        "locator; si no, resolve_service('task_registry') es None y crashea."
    )
    # 2. No debe propagarse el error de bootstrap ('get_task_info' sobre None).
    msg = getattr(resp, "message", "") or ""
    assert "get_task_info" not in msg, f"handle_task crasheó con bootstrap vacío: {msg}"
