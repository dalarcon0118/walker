"""test_task_context_advance_bdd.py — la tarea activa viaja con su spec (P1.6).

El plan de vuelo dice qué se está haciendo; la spec guarda con qué identidad se
captura el conocimiento. Cuando los dos se desincronizan, la captura resuelve la
spec por `in_progress` y una tarea nueva hereda la identidad de la vieja
(medido 2026-11-19: `prepare-seeds-manifest` se capturó como
`skill-token-memory-health`).

Regla: cambiar la tarea en el plan —o poner una spec en progreso— ancla el
`task_context` de esa spec y avanza el estado del workflow. Si la tarea no
existe en el sprint, se avisa (no se inventa una spec).
"""
from __future__ import annotations

import pytest

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers import _di
from fast_mcp.features.tasks.handlers.session import session_plan, spec_start
from fast_mcp.features.tasks.handlers.spec_status import spec_status


class FakeSprintService:
    """Delta mínimo: sesión + specs, sin tocar el storage real."""

    def __init__(self) -> None:
        self.session = {
            "session_id": "S-1",
            "spring_id": "SPRING-1",
            "status": "active",
            "flight_plan": {},
        }
        self.specs = [
            {"spec_id": "SPEC-002", "status": "draft", "objective": "primera",
             "files": ["a.py"], "task_context": {}},
            {"spec_id": "SPEC-003", "status": "in_progress", "objective": "vieja",
             "files": ["b.py"], "task_context": {"task_id": "SPEC-003",
                                                 "session_id": "S-0"}},
        ]

    # sesiones
    def get_session(self, session_id):
        return self.session if session_id == self.session["session_id"] else None

    def update_session_plan(self, session_id, patch):
        if self.get_session(session_id) is None:
            return None
        self.session.setdefault("flight_plan", {}).update(patch)
        return self.session["flight_plan"]

    def get_session_plan(self, session_id):
        return (self.get_session(session_id) or {}).get("flight_plan") or None

    def list_sessions(self, status="", project_id="", limit=0):
        return [self.session]

    # sprints/specs
    def get_active_spring(self):
        return {"spring_id": "SPRING-1"}

    def list_springs(self):
        return [{"spring_id": "SPRING-1"}]

    def get_specs(self, spring_id, status=""):
        return list(self.specs)

    def get_spec(self, spring_id, spec_id):
        return next((s for s in self.specs if s["spec_id"] == spec_id), None)

    def update_spec(self, spring_id, spec_id, updates):
        spec = self.get_spec(spring_id, spec_id)
        if spec is None:
            return None
        spec.update(updates)
        return spec

    def update_spec_status(self, spring_id, spec_id, status, artifacts=None):
        spec = self.get_spec(spring_id, spec_id)
        if spec is None:
            return None
        spec["status"] = status
        if artifacts:
            spec.update(artifacts)
        return spec


@pytest.fixture()
def svc(monkeypatch):
    service = FakeSprintService()
    recorded: list[str] = []
    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda chain_dir: service)
    monkeypatch.setattr(
        _di, "_TASK_RECORDER", lambda chain_dir, task_id: recorded.append(task_id) or True
    )
    service.recorded = recorded
    return service


def _ctx() -> ExecutionContext:
    return ExecutionContext.create(chain_dir="/tmp/proj", session_id="S-1")


def test_session_plan_ancla_la_spec_de_la_tarea_nueva(svc):
    out = session_plan(
        _ctx(),
        {"session_id": "S-1", "mode": "set", "task": "SPEC-002"},
    )

    assert out.ok
    assert out.data["task_link"]["linked"] == "SPEC-002"
    spec = svc.get_spec("SPRING-1", "SPEC-002")
    assert spec["task_context"] == {"task_id": "SPEC-002", "session_id": "S-1"}
    assert "SPEC-002" in svc.recorded
    # La captura ya resuelve por id exacto, sin caer a la `in_progress` vieja.
    resolved = _di.current_spec("/tmp/proj", task_id="SPEC-002", exact=True)
    assert resolved["spec_id"] == "SPEC-002"


def test_session_plan_con_tarea_inexistente_no_inventa_spec(svc):
    out = session_plan(
        _ctx(),
        {"session_id": "S-1", "mode": "set", "task": "TAREA-QUE-NO-EXISTE"},
    )

    assert out.ok, "el plan se guarda igual: la tarea puede no ser una spec"
    assert out.data["plan"]["task"] == "TAREA-QUE-NO-EXISTE"
    assert out.data["task_link"]["linked"] == ""
    assert "TAREA-QUE-NO-EXISTE" in out.data["task_link"]["reason"]
    assert svc.recorded == [], "sin spec no se avanza el estado del workflow"


def test_session_plan_sin_cambio_de_tarea_no_toca_specs(svc):
    session_plan(_ctx(), {"session_id": "S-1", "mode": "set", "task": "SPEC-002"})
    svc.recorded.clear()
    svc.get_spec("SPRING-1", "SPEC-002")["task_context"]["session_id"] = "S-OTRA"

    out = session_plan(_ctx(), {"session_id": "S-1", "mode": "set", "next": "seguir"})

    assert out.ok
    assert "task_link" not in out.data
    assert svc.get_spec("SPRING-1", "SPEC-002")["task_context"]["session_id"] == "S-OTRA"


def test_spec_status_in_progress_mueve_el_plan_y_el_workflow(svc):
    out = spec_status(
        _ctx(), {"spec_id": "SPEC-002", "status": "in_progress", "session_id": "S-1"}
    )

    assert out.ok
    assert out.data["task_id"] == "SPEC-002"
    assert out.data["task_link"]["linked"] == "SPEC-002"
    assert svc.session["flight_plan"]["task"] == "SPEC-002"
    assert svc.get_spec("SPRING-1", "SPEC-002")["task_context"]["task_id"] == "SPEC-002"
    assert svc.recorded == ["SPEC-002"]


def test_spec_status_completed_no_mueve_la_tarea_activa(svc):
    out = spec_status(
        _ctx(),
        {
            "spec_id": "SPEC-003",
            "status": "completed",
            "session_id": "S-1",
            "completed_at": "2026-11-19T00:00:00Z",
            "commit_hash": "abc123",
            "review_verdict": "approved",
            "evidence": ["tests verdes"],
        },
    )

    assert out.ok
    assert "task_link" not in out.data
    assert "task" not in svc.session["flight_plan"]
    assert svc.recorded == []


def test_spec_start_deja_la_identidad_canonica(svc):
    out = spec_start(_ctx(), {"spec_id": "SPEC-002", "session_id": "S-1"})

    assert out.ok
    spec = svc.get_spec("SPRING-1", "SPEC-002")
    assert spec["task_context"]["task_id"] == "SPEC-002"
    assert svc.session["flight_plan"]["task"] == "SPEC-002"
    assert svc.recorded == ["SPEC-002"]
