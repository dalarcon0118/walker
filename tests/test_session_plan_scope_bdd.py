"""BDD: `session_plan mode=add|remove` para no reenviar el alcance completo.

Medido 2026-11-18: el alcance del plan llegó a 55 entradas y `mode:"set"` exige
reenviarlas todas para agregar un archivo; el error de alcance imprime el
comando entero. `add`/`remove` tocan solo lo que cambió.
"""
from __future__ import annotations

import pytest

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers import _di
from fast_mcp.features.tasks.handlers.session import session_plan


class FakeService:
    def __init__(self) -> None:
        self.session = {
            "session_id": "s1",
            "flight_plan": {"goal": "g", "scope": ["a.py", "b.py"]},
        }

    def get_session(self, session_id):
        return self.session

    def update_session_plan(self, session_id, patch):
        self.session["flight_plan"] = {**self.session["flight_plan"], **patch}
        return self.session["flight_plan"]


@pytest.fixture()
def svc():
    service = FakeService()
    _di.configure_sprint(lambda chain_dir: service)
    yield service
    _di.configure_sprint(None)


def _ctx() -> ExecutionContext:
    return ExecutionContext.create(chain_dir="/tmp/proj", session_id="s1")


def test_add_suma_sin_duplicar(svc):
    out = session_plan(_ctx(), {"session_id": "s1", "mode": "add", "scope": ["b.py", "c.py"]})

    assert out.ok, out.error
    assert svc.session["flight_plan"]["scope"] == ["a.py", "b.py", "c.py"]
    assert out.data["scope_size"] == 3


def test_add_acepta_path_suelto(svc):
    out = session_plan(_ctx(), {"session_id": "s1", "mode": "add", "path": "c.py"})

    assert out.ok
    assert "c.py" in svc.session["flight_plan"]["scope"]


def test_remove_quita_solo_lo_indicado(svc):
    out = session_plan(_ctx(), {"session_id": "s1", "mode": "remove", "scope": ["a.py"]})

    assert out.ok
    assert svc.session["flight_plan"]["scope"] == ["b.py"]


def test_add_sin_scope_falla_explicito(svc):
    out = session_plan(_ctx(), {"session_id": "s1", "mode": "add"})

    assert out.ok is False
    assert "scope" in out.error
