"""BDD: `spec_start` une una spec existente (roadmap) a la sesión y la inicia."""

from __future__ import annotations

import pytest

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers import _di
from fast_mcp.features.tasks.handlers.session import spec_start


class FakeService:
    def __init__(self) -> None:
        self.specs = {
            "SPEC-001": {
                "spec_id": "SPEC-001",
                "status": "specified",
                "files": ["agent-plugins/x.js"],
                "task_context": {},
            }
        }
        self.plans: dict[str, dict] = {}
        self.session = {"session_id": "s1", "spring_id": "SPR-INTEG"}

    def get_session(self, session_id):
        return self.session

    def get_spec(self, spring_id, spec_id):
        return self.specs.get(spec_id)

    def update_spec(self, spring_id, spec_id, updates):
        self.specs[spec_id].update(updates)
        return self.specs[spec_id]

    def update_session_plan(self, session_id, patch):
        self.plans[session_id] = {**self.plans.get(session_id, {}), **patch}
        return self.plans[session_id]


@pytest.fixture()
def svc():
    service = FakeService()
    _di.configure_sprint(lambda chain_dir: service)
    yield service
    _di.configure_sprint(None)


def _ctx() -> ExecutionContext:
    return ExecutionContext.create(chain_dir="/tmp/proj", session_id="s1")


def test_inicia_la_spec_y_la_liga_a_la_sesion(svc):
    out = spec_start(_ctx(), {"session_id": "s1", "spec_id": "SPEC-001"})

    assert out.ok
    spec = svc.specs["SPEC-001"]
    assert spec["status"] == "in_progress"
    assert spec["task_context"]["task_id"] == "SPEC-001"
    assert spec["task_context"]["session_id"] == "s1"
    assert svc.plans["s1"]["task"] == "SPEC-001"
    assert svc.plans["s1"]["scope"] == ["agent-plugins/x.js"]


def test_resuelve_el_sprint_por_sesion(svc):
    out = spec_start(_ctx(), {"session_id": "s1", "spec_id": "SPEC-001"})

    assert out.data["spring_id"] == "SPR-INTEG"


def test_spec_desconocida_falla(svc):
    out = spec_start(_ctx(), {"session_id": "s1", "spec_id": "SPEC-999"})

    assert out.ok is False
    assert "desconocida" in out.error


def test_sin_sprint_falla(svc):
    svc.session = {"session_id": "s1", "spring_id": ""}

    out = spec_start(_ctx(), {"session_id": "s1", "spec_id": "SPEC-001"})

    assert out.ok is False
    assert "spring_id" in out.error


def test_spec_start_recuerda_la_tarea_en_el_workflow(svc):
    """El delta sabía la tarea; el estado del workflow seguía en la anterior.

    Medido 2026-11-18: arrancó SPEC-001 y el prompt seguía mostrando SPEC-002,
    así que el diseño se armaba con los datos de otra spec.
    """
    recorded: list[tuple[str, str]] = []
    _di.configure_task_recorder(lambda chain_dir, task_id: recorded.append((chain_dir, task_id)) or True)
    try:
        out = spec_start(_ctx(), {"session_id": "s1", "spec_id": "SPEC-001"})
    finally:
        _di.configure_task_recorder(None)

    assert out.ok
    assert recorded == [("/tmp/proj", "SPEC-001")]
    assert out.data["task_recorded"] is True
