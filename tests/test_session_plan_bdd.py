"""BDD: ADR-WALKER-080 — plan de vuelo de la sesión.

El plan es el estado que el agente debe mantener a la vista: goal, criterios de
aceptación, tarea actual, alcance y próximo paso. Vive en el registro para
sobrevivir recargas del plugin.
"""

from __future__ import annotations

import pytest

from fast_mcp.features.delta_tracking.application.services.sprint_service import SprintService
from fast_mcp.features.delta_tracking.domain.entities import Session
from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers import _di
from fast_mcp.features.tasks.handlers.session import session_plan


class FakeRepo:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    def get_session(self, session_id: str):
        return self.sessions.get(session_id)

    def save_session(self, session: Session) -> None:
        self.sessions[session.session_id] = session

    def list_sessions(self, status: str = "", project_id: str = ""):
        return list(self.sessions.values())


@pytest.fixture()
def svc():
    repo = FakeRepo()
    service = SprintService(repo=repo)
    service.open_session("s1", project_id="p")
    _di.configure_sprint(lambda chain_dir: service)
    yield service
    _di.configure_sprint(None)


def _ctx() -> ExecutionContext:
    return ExecutionContext.create(chain_dir="/tmp/proj", session_id="s1")


def test_set_deriva_criterios_del_goal(svc):
    out = session_plan(
        _ctx(),
        {
            "session_id": "s1",
            "mode": "set",
            "goal": "Arreglar el login; debe mantener sesión y sin romper los tests",
        },
    )

    assert out.ok
    plan = out.data["plan"]
    assert plan["goal"].startswith("Arreglar el login")
    assert plan["acceptance"], "debe derivar criterios ('debe…', 'sin romper…')"
    assert plan["updated_at"]


def test_set_hace_merge_y_conserva_lo_previo(svc):
    session_plan(_ctx(), {"session_id": "s1", "mode": "set", "goal": "goal uno"})
    out = session_plan(
        _ctx(),
        {"session_id": "s1", "mode": "set", "task": "t1", "scope": ["a.py"], "next": "implementar"},
    )

    plan = out.data["plan"]
    assert plan["goal"] == "goal uno"
    assert plan["task"] == "t1"
    assert plan["scope"] == ["a.py"]
    assert plan["next"] == "implementar"


def test_get_devuelve_el_plan(svc):
    session_plan(_ctx(), {"session_id": "s1", "mode": "set", "goal": "g"})

    out = session_plan(_ctx(), {"session_id": "s1", "mode": "get"})

    assert out.ok and out.data["plan"]["goal"] == "g"


def test_goal_sin_criterios_pide_definirlos(svc):
    out = session_plan(_ctx(), {"session_id": "s1", "mode": "set", "goal": "mejorar cosas"})

    plan = out.data["plan"]
    assert plan["acceptance"] == []
    assert "criterios" in plan["next"]


def test_sesion_desconocida_falla(svc):
    out = session_plan(_ctx(), {"session_id": "no-existe", "mode": "set", "goal": "g"})

    assert out.ok is False
    assert "desconocida" in out.error


def test_set_sin_campos_falla(svc):
    out = session_plan(_ctx(), {"session_id": "s1", "mode": "set"})

    assert out.ok is False
    assert "nada para actualizar" in out.error


def test_get_sin_plan_devuelve_vacio(svc):
    out = session_plan(_ctx(), {"session_id": "s1", "mode": "get"})

    assert out.ok and out.data["plan"] == {}
