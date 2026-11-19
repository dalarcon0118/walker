"""BDD: sprint_planning session-aware (ADR-WALKER-077/079).

Dos defectos que se corrigen acá:
  - `plan_sprint` usaba `project_id or ctx.session_id` como project_id del
    sprint: metía el id de la SESIÓN en un campo de PROYECTO.
  - `close_sprint` sin ids cerraba el sprint activo del proyecto, que puede ser
    el de otra sesión. Ahora resuelve por sesión y, si usa el default, lo dice.
"""

from __future__ import annotations

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers.sprint_planning import _build_handlers


class FakeService:
    def __init__(self) -> None:
        self.springs: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.active: dict | None = None

    def create_spring(
        self, spring_id, goal="", status="planning", project_id="", session_id="", context=None, **kw
    ):
        spring = {
            "spring_id": spring_id,
            "goal": goal,
            "status": status,
            "project_id": project_id,
            "session_id": session_id,
            "context": context or {},
        }
        self.springs[spring_id] = spring
        return spring

    def add_spec(self, **kw):
        return {"_id": "SPEC-1", **kw}

    def get_specs(self, spring_id, status=""):
        return []

    def get_session(self, session_id):
        return self.sessions.get(session_id)

    def open_session(self, session_id, **kw):
        self.sessions.setdefault(session_id, {"session_id": session_id})
        self.sessions[session_id].update({k: v for k, v in kw.items() if v})
        return self.sessions[session_id]

    def get_active_spring(self):
        return self.active

    def set_spring_status(self, spring_id, status):
        self.springs[spring_id]["status"] = status

    def close_spring(self, spring_id, force=False, reason=""):
        self.springs[spring_id]["status"] = "closed"
        return {
            "ok": True,
            "spring_id": spring_id,
            "status": "closed",
            "settled": [],
            "open": [],
            "forced": False,
        }

    def transition_spring(self, spring_id, status):
        self.springs[spring_id]["status"] = status
        return {"ok": True}


def _ctx() -> ExecutionContext:
    return ExecutionContext.create(chain_dir="/tmp/proj", session_id="s1")


def test_plan_sprint_no_mete_la_sesion_como_project_id():
    svc = FakeService()
    plan, *_ = _build_handlers(svc)

    result = plan(_ctx(), {"goal": "un goal", "session_id": "s1", "project_id": "p"})

    spring_id = result.data["spring_id"]
    assert spring_id.startswith("SPR-S")
    assert svc.springs[spring_id]["project_id"] == "p"
    assert svc.springs[spring_id]["session_id"] == "s1"
    assert svc.sessions["s1"]["spring_id"] == spring_id


def test_plan_sprint_sin_project_no_usa_la_sesion_como_proyecto():
    svc = FakeService()
    plan, *_ = _build_handlers(svc)

    result = plan(_ctx(), {"goal": "g", "session_id": "s1"})

    spring = svc.springs[result.data["spring_id"]]
    assert spring["project_id"] == ""  # vacío, nunca "s1"
    assert spring["session_id"] == "s1"


def test_close_sprint_resuelve_por_sesion_y_no_por_el_activo():
    svc = FakeService()
    svc.create_spring("SPR-DE-LA-SESION", status="active", session_id="s1")
    svc.create_spring("SPR-DE-OTRA", status="active", session_id="s2")
    svc.open_session("s1", spring_id="SPR-DE-LA-SESION")
    svc.active = {"spring_id": "SPR-DE-OTRA"}
    _, _, _, close = _build_handlers(svc)

    result = close(_ctx(), {"session_id": "s1"})

    assert result.data["spring_id"] == "SPR-DE-LA-SESION"
    assert result.data["resolved_by"] == "session"
    assert svc.springs["SPR-DE-LA-SESION"]["status"] == "closed"
    assert svc.springs["SPR-DE-OTRA"]["status"] == "active"


def test_close_sprint_sin_contexto_declara_el_default():
    svc = FakeService()
    svc.create_spring("SPR-ACTIVO", status="active")
    svc.active = {"spring_id": "SPR-ACTIVO"}
    _, _, _, close = _build_handlers(svc)

    result = close(_ctx(), {})

    assert result.data["spring_id"] == "SPR-ACTIVO"
    assert result.data["resolved_by"] == "project_default"
