"""BDD: `session_bind` ata una sesión de chat a un sprint (delta-sprint).

Cubre las tres decisiones del protocolo de continuidad y la idempotencia:
  - auto sin trabajo previo       -> no_work
  - auto con sprint de otro       -> resume_available (con goal y tareas)
  - new                           -> crea el sprint de la sesión
  - new repetido                  -> no crea otro (idempotente)
  - continue                      -> suma la sesión al sprint previo como hija
"""

from __future__ import annotations

import pytest

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers import _di
from fast_mcp.features.tasks.handlers.session import session_bind


class FakeSprintService:
    def __init__(self) -> None:
        self.springs: list[dict] = []
        self.specs: dict[str, list[dict]] = {}
        self.sessions: dict[str, dict] = {}
        self.plans: dict[str, dict] = {}

    def list_springs(self, status: str = "") -> list[dict]:
        return list(self.springs)

    def get_spring(self, spring_id: str) -> dict | None:
        return next((s for s in self.springs if s["spring_id"] == spring_id), None)

    def create_spring(
        self,
        spring_id: str,
        goal: str = "",
        status: str = "planning",
        project_id: str = "",
        session_id: str = "",
        **extra,
    ) -> dict:
        spring = {
            "spring_id": spring_id,
            "goal": goal,
            "status": status,
            "project_id": project_id,
            "session_id": session_id,
            "context": extra.get("context") or {},
        }
        self.springs.append(spring)
        return spring

    def update_spring(self, spring_id: str, data: dict) -> None:
        spring = self.get_spring(spring_id)
        if spring:
            spring.update({k: v for k, v in data.items() if v is not None})

    def get_specs(self, spring_id: str, status: str = "") -> list[dict]:
        return list(self.specs.get(spring_id, []))

    def open_session(self, session_id: str, **kw) -> dict:
        self.sessions.setdefault(session_id, {"session_id": session_id})
        self.sessions[session_id].update({k: v for k, v in kw.items() if v})
        return self.sessions[session_id]

    def last_session(self, project_id: str = "") -> dict | None:
        return None

    def update_session_plan(self, session_id: str, patch: dict) -> dict:
        self.plans[session_id] = {**self.plans.get(session_id, {}), **patch}
        return self.plans[session_id]

    def get_session_plan(self, session_id: str) -> dict | None:
        return self.plans.get(session_id)


@pytest.fixture()
def svc():
    service = FakeSprintService()
    _di.configure_sprint(lambda chain_dir: service)
    yield service
    _di.configure_sprint(None)


def _ctx(session_id: str = "s1") -> ExecutionContext:
    return ExecutionContext.create(chain_dir="/tmp/proj", session_id=session_id)


def _data(result) -> dict:
    assert result.ok, result.error
    return result.data


def test_auto_sin_trabajo_previo_pide_crear(svc):
    out = _data(session_bind(_ctx(), {"session_id": "s1", "mode": "auto"}))
    assert out["decision"] == "no_work"
    assert "mode=new" in out["suggestion"]


def test_auto_con_sprint_previo_ofrece_continuar(svc):
    svc.create_spring("SPR-OLD", goal="login roto", status="active", session_id="s0")
    svc.specs["SPR-OLD"] = [{"spec_id": "t1", "objective": "arreglar login", "task_context": {"task_id": "t1", "session_id": "s0"}}]

    out = _data(session_bind(_ctx(), {"session_id": "s1", "mode": "auto"}))
    assert out["decision"] == "resume_available"
    assert out["previous"]["spring_id"] == "SPR-OLD"
    assert out["previous"]["tasks_count"] == 1
    assert "mode=continue" in out["suggestion"] and "mode=new" in out["suggestion"]


def test_new_crea_sprint_de_la_sesion(svc):
    out = _data(session_bind(_ctx(), {"session_id": "s1", "mode": "new", "objective": "arreglar el login"}))
    assert out["decision"] == "bound" and out["created"] is True
    spring = svc.springs[0]
    assert spring["session_id"] == "s1"
    assert spring["goal"] == "arreglar el login"
    assert spring["context"]["session_ids"] == ["s1"]


def test_new_repetido_es_idempotente(svc):
    _data(session_bind(_ctx(), {"session_id": "s1", "mode": "new", "objective": "uno"}))
    out = _data(session_bind(_ctx(), {"session_id": "s1", "mode": "new", "objective": "dos"}))
    assert out["created"] is False
    assert len(svc.springs) == 1
    assert svc.springs[0]["goal"] == "uno"


def test_continue_suma_la_sesion_como_hija(svc):
    svc.create_spring("SPR-OLD", goal="login roto", status="active", session_id="s0", context={"session_ids": ["s0"]})
    out = _data(session_bind(_ctx(), {"session_id": "s1", "mode": "continue"}))
    assert out["decision"] == "bound" and out["continued"] is True
    spring = svc.springs[0]
    assert spring["context"]["session_ids"] == ["s0", "s1"]
    assert spring["context"]["parent_session_id"] == "s0"


def test_continue_sin_sprint_falla_con_indicacion(svc):
    out = session_bind(_ctx(), {"session_id": "s1", "mode": "continue"})
    assert out.ok is False
    assert "mode=new" in out.error


def test_bind_avisa_si_el_sprint_esta_pausado(svc):
    svc.create_spring("SPR-P", goal="en pausa", status="paused", session_id="s1", context={"session_ids": ["s1"]})

    out = _data(session_bind(_ctx(), {"session_id": "s1", "mode": "auto"}))

    assert out["decision"] == "bound"
    assert "PAUSADO" in out["suggestion"]
    assert "sprint_status" in out["suggestion"]


def test_new_inicializa_el_plan_de_vuelo(svc):
    _data(session_bind(_ctx(), {"session_id": "s1", "mode": "new", "objective": "debe arreglar el login; sin romper los tests"}))

    plan = svc.get_session_plan("s1")
    assert plan["goal"].startswith("debe arreglar")
    assert plan["acceptance"]
    assert plan["task"] == ""
    assert plan["next"]


def test_continue_con_spring_explicito_se_une_al_sprint(svc):
    svc.create_spring("SPR-INTEG", goal="roadmap del proyecto", status="planning", project_id="p")

    out = _data(session_bind(_ctx(), {"session_id": "s1", "spring_id": "SPR-INTEG", "mode": "continue"}))

    assert out["decision"] == "bound" and out["continued"] is True
    assert out["bound"]["spring_id"] == "SPR-INTEG"
    spring = svc.get_spring("SPR-INTEG")
    assert "s1" in spring["context"]["session_ids"]
    assert svc.get_session_plan("s1")["goal"] == "roadmap del proyecto"


def test_new_con_spring_existente_no_lo_pisa(svc):
    svc.create_spring("SPR-INTEG", goal="roadmap del proyecto", status="planning", project_id="p")

    out = _data(session_bind(_ctx(), {"session_id": "s1", "spring_id": "SPR-INTEG", "mode": "new"}))

    assert out["created"] is False and out["continued"] is True
    assert svc.get_spring("SPR-INTEG")["goal"] == "roadmap del proyecto"
    assert len(svc.springs) == 1


def test_spring_explicito_desconocido_falla(svc):
    out = session_bind(_ctx(), {"session_id": "s1", "spring_id": "NO-EXISTE", "mode": "continue"})

    assert out.ok is False
    assert "desconocido" in out.error


# ── Ciclo de vida post-cierre (el sprint `closed` es terminal) ──────────


def _closed_spring(svc, session_id="s1", spring_id=None):
    from fast_mcp.features.tasks.handlers.session import _short_hash

    spring_id = spring_id or f"SPR-S{_short_hash(session_id)}"
    return svc.create_spring(
        spring_id,
        goal="ciclo anterior",
        status="closed",
        session_id=session_id,
        context={"session_ids": [session_id]},
    )


def test_auto_con_sprint_cerrado_no_lo_reporta_bound(svc):
    _closed_spring(svc)

    out = _data(session_bind(_ctx(), {"session_id": "s1", "mode": "auto"}))

    assert out["decision"] == "no_work"
    assert out["previous"]["spring_id"].startswith("SPR-S")
    assert out["previous"]["status"] == "closed"
    assert "mode=new" in out["suggestion"]


def test_new_con_sprint_cerrado_crea_otro_y_reposiciona(svc):
    from fast_mcp.features.tasks.handlers.session import _short_hash

    base_id = f"SPR-S{_short_hash('s1')}"
    _closed_spring(svc, spring_id=base_id)
    calls: list[str] = []
    _di.configure_workflow_reset(
        lambda chain_dir: calls.append(chain_dir) or {"ok": True, "changed": True}
    )
    try:
        out = _data(session_bind(_ctx(), {"session_id": "s1", "mode": "new", "objective": "ciclo nuevo"}))
    finally:
        _di.configure_workflow_reset(None)

    assert out["decision"] == "bound" and out["created"] is True
    assert out["bound"]["spring_id"] == f"{base_id}-2"
    assert out["bound"]["status"] == "active"
    assert calls == ["/tmp/proj"]
    assert len(svc.springs) == 2
    assert svc.get_spring(base_id)["status"] == "closed"  # el anterior no se toca


def test_new_no_crea_si_no_puede_reposicionar_el_workflow(svc):
    _closed_spring(svc)
    _di.configure_workflow_reset(lambda chain_dir: {"ok": False, "error": "boom"})
    try:
        out = session_bind(_ctx(), {"session_id": "s1", "mode": "new", "objective": "x"})
    finally:
        _di.configure_workflow_reset(None)

    assert out.ok is False
    assert "reposicionar" in out.error
    assert len(svc.springs) == 1  # no se creó nada


def test_continue_con_sprint_cerrado_falla(svc):
    _closed_spring(svc)

    out = session_bind(_ctx(), {"session_id": "s1", "mode": "continue"})

    assert out.ok is False
    assert "cerrado" in out.error and "mode=new" in out.error


def test_new_con_spring_explicito_cerrado_falla(svc):
    _closed_spring(svc, spring_id="SPR-CERRADO")

    out = session_bind(
        _ctx(), {"session_id": "s1", "spring_id": "SPR-CERRADO", "mode": "new"}
    )

    assert out.ok is False
    assert "cerrado" in out.error
