"""BDD: registro de sesiones (ciclo de vida + jerarquía) en el delta.

Se prueba el SprintService REAL con un repo falso: la lógica vive ahí, no en el
handler, así que el test no la duplica.
"""

from __future__ import annotations

from fast_mcp.features.delta_tracking.application.services.sprint_service import SprintService
from fast_mcp.features.delta_tracking.domain.entities import Session


class FakeRepo:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    def save_session(self, session: Session) -> None:
        self.sessions[session.session_id] = session

    def list_sessions(self, status: str = "", project_id: str = "") -> list[Session]:
        out = list(self.sessions.values())
        if status:
            out = [s for s in out if s.status == status]
        if project_id:
            out = [s for s in out if s.project_id == project_id]
        return out


def _svc() -> SprintService:
    return SprintService(repo=FakeRepo())


def test_open_crea_y_es_idempotente():
    svc = _svc()
    first = svc.open_session("s1", project_id="p", agent="build", goal="uno")
    again = svc.open_session("s1", agent="build", goal="dos")

    assert again["started_at"] == first["started_at"]
    assert again["status"] == "active"
    assert again["goal"] == "dos"
    assert len(svc.list_sessions()) == 1


def test_open_conserva_project_y_permite_ligar_sprint():
    svc = _svc()
    svc.open_session("s1", project_id="p")
    svc.open_session("s1")
    linked = svc.link_session_spring("s1", "SPR-1", goal="g")

    assert linked["project_id"] == "p"
    assert linked["spring_id"] == "SPR-1"
    assert linked["goal"] == "g"


def test_close_marca_estado_y_outcome():
    svc = _svc()
    svc.open_session("s1")
    closed = svc.close_session("s1", outcome="succeeded")

    assert closed["status"] == "closed"
    assert closed["outcome"] == "succeeded"
    assert closed["ended_at"]


def test_reabrir_limpia_el_cierre():
    svc = _svc()
    svc.open_session("s1")
    svc.close_session("s1", outcome="failed")
    reopened = svc.open_session("s1")

    assert reopened["status"] == "active"
    assert reopened["ended_at"] == ""


def test_list_ordena_por_inicio_y_respeta_limit():
    svc = _svc()
    svc.open_session("vieja")
    svc.open_session("nueva")
    # started_at con precisión de microsegundos: forzamos orden explícito
    repo = svc._repo
    repo.sessions["vieja"].started_at = "2026-01-01T00:00:00+00:00"
    repo.sessions["nueva"].started_at = "2026-02-01T00:00:00+00:00"

    sessions = svc.list_sessions(limit=1)

    assert [s["session_id"] for s in sessions] == ["nueva"]
    assert svc.last_session()["session_id"] == "nueva"


def test_list_filtra_por_estado():
    svc = _svc()
    svc.open_session("abierta")
    svc.open_session("cerrada")
    svc.close_session("cerrada", outcome="succeeded")

    assert [s["session_id"] for s in svc.list_sessions(status="active")] == ["abierta"]
    assert [s["session_id"] for s in svc.list_sessions(status="closed")] == ["cerrada"]


def test_jerarquia_parent_session():
    svc = _svc()
    svc.open_session("padre")
    hija = svc.open_session("hija", parent_session_id="padre")

    assert hija["parent_session_id"] == "padre"


def test_children_derivados_del_parent():
    svc = _svc()
    svc.open_session("padre")
    svc.open_session("hija-1", parent_session_id="padre")
    svc.open_session("hija-2", parent_session_id="padre")
    svc.open_session("nieto", parent_session_id="hija-1")

    by_id = {s["session_id"]: s for s in svc.list_sessions()}

    assert by_id["padre"]["children"] == ["hija-1", "hija-2"]
    assert by_id["hija-1"]["children"] == ["nieto"]
    assert by_id["nieto"]["children"] == []


def test_session_tree_anida_y_no_pierde_huerfanos():
    svc = _svc()
    svc.open_session("padre")
    svc.open_session("hija", parent_session_id="padre")
    svc.open_session("nieto", parent_session_id="hija")
    svc.open_session("huerfana", parent_session_id="no-existe")

    roots = svc.session_tree()
    ids = {r["session_id"] for r in roots}

    assert ids == {"padre", "huerfana"}
    padre = next(r for r in roots if r["session_id"] == "padre")
    assert [c["session_id"] for c in padre["children"]] == ["hija"]
    assert [c["session_id"] for c in padre["children"][0]["children"]] == ["nieto"]


def test_tree_respeta_filtro_de_proyecto():
    svc = _svc()
    svc.open_session("a", project_id="p1")
    svc.open_session("b", project_id="p2")

    assert [r["session_id"] for r in svc.session_tree(project_id="p1")] == ["a"]
