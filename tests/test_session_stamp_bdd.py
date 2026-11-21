"""test_session_stamp_bdd.py — el cierre deja un sello de verificación (AE-INV-12).

`AGENT-EFFICIENCY.md` §8: sin sello, "cerrado" y "verificado" son la misma
palabra. El sello convierte el cierre en un artefacto consultable:

    {kind: tests|predicates|e2e|command|none, verified, level, weak, criteria,
     rounds, source, at}

Reglas que se prueban acá:
  * un sello ausente NO se inventa: queda `kind=none`, `source=unstated`.
  * un `kind` desconocido se degrada a `none` (nunca se acepta como verde).
  * `verified` no puede ser True con `kind=none` (contradicción silenciosa).
  * el sello viaja en la respuesta del cierre y en `session_context`.
"""
from __future__ import annotations

import pytest

from fast_mcp.features.delta_tracking.application.services.sprint_service import (
    SprintService,
)
from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers import _di
from fast_mcp.features.tasks.handlers import knowledge as knowledge_handlers
from fast_mcp.features.tasks.handlers import session as session_handlers


class _FakeRepo:
    """DeltaRepository mínimo: guarda sesiones en memoria (sin sqlite)."""

    def __init__(self):
        self.sessions: dict[str, dict] = {}

    def get_session(self, session_id):
        return self.sessions.get(session_id)

    def save_session(self, session):
        self.sessions[session.session_id] = session


class _FakeSprintService:
    def __init__(self):
        self.repo = _FakeRepo()
        self.stamp = {}
        self.cost_usd = None
        self.tokens = None
        self.active_spring = None
        self.specs: list[dict] = []

    def close_session(self, session_id, outcome="", stamp=None, cost_usd=None, tokens=None):
        self.stamp = stamp or {}
        self.cost_usd = cost_usd
        self.tokens = tokens
        return {
            "session_id": session_id,
            "status": "closed",
            "stamp": self.stamp,
            "cost_usd": cost_usd,
            "tokens": tokens or {},
        }

    def get_active_spring(self):
        return self.active_spring

    def list_springs(self):
        return [self.active_spring] if self.active_spring else []

    def get_specs(self, spring_id):
        return self.specs


@pytest.fixture()
def fake_service(monkeypatch):
    service = _FakeSprintService()
    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda chain_dir: service)
    monkeypatch.setattr(knowledge_handlers, "capture_knowledge", _stub_capture)
    return service


def _stub_capture(ctx, data):
    from fast_mcp.features.tasks.domain.task_registry import TaskResult

    return TaskResult.success(data={"ok": True})


def _ctx(tmp_path, task_id="T-1"):
    return ExecutionContext.create(chain_dir=str(tmp_path), task_id=task_id)


# ── normalización ────────────────────────────────────────────────────────


def test_sin_sello_no_se_inventa_queda_none_y_unstated():
    stamp = session_handlers.normalize_stamp(None)
    assert stamp["kind"] == "none"
    assert stamp["verified"] is False
    assert stamp["source"] == "unstated"


def test_tipo_de_sello_desconocido_se_degrada_a_none():
    stamp = session_handlers.normalize_stamp({"kind": "porque-si", "verified": True})
    assert stamp["kind"] == "none"
    assert stamp["verified"] is False, "un kind inválido no puede quedar como verde"


def test_verified_no_puede_contradecir_kind_none():
    stamp = session_handlers.normalize_stamp({"kind": "none", "verified": True})
    assert stamp["verified"] is False


def test_sello_completo_conserva_evidencia():
    stamp = session_handlers.normalize_stamp(
        {
            "kind": "tests",
            "verified": True,
            "level": "tool",
            "criteria": 3,
            "rounds": 2,
            "weak": False,
            "at": "2026-11-20T00:00:00+00:00",
        }
    )
    assert stamp == {
        "kind": "tests",
        "verified": True,
        "level": "tool",
        "weak": False,
        "criteria": 3,
        "rounds": 2,
        "source": "plugin",
        "verified_at": "",
        "at": "2026-11-20T00:00:00+00:00",
    }


def test_sello_con_rounds_basura_no_rompe_el_cierre():
    stamp = session_handlers.normalize_stamp({"kind": "predicates", "rounds": "x"})
    assert stamp["rounds"] == 0
    assert stamp["kind"] == "predicates"


# ── cierre ───────────────────────────────────────────────────────────────


def test_el_cierre_persiste_el_sello(tmp_path, fake_service):
    out = session_handlers.session_close(
        _ctx(tmp_path),
        {
            "session_id": "S-1",
            "outcome": "succeeded",
            "stamp": {"kind": "predicates", "verified": True, "level": "predicates"},
        },
    )
    assert out.ok
    assert fake_service.stamp["kind"] == "predicates"
    assert fake_service.stamp["verified"] is True
    assert out.data["session"]["stamp"]["kind"] == "predicates"


def test_el_cierre_sin_sello_lo_declara(tmp_path, fake_service):
    out = session_handlers.session_close(_ctx(tmp_path), {"session_id": "S-1"})
    assert out.ok
    assert fake_service.stamp["kind"] == "none"
    assert fake_service.stamp["source"] == "unstated"
    # Sin specs colgadas, el cierre declara que el flujo se siguió.
    assert out.data["flow"] == "workflow"
    assert out.data["orphan_specs"] == []
    assert fake_service.stamp.get("flow", "workflow") == "workflow"


def test_cierre_con_spec_in_progress_marca_flow_tools(tmp_path, fake_service):
    """La spec nace con implement_task y nunca entra a un nodo: es un dato."""
    fake_service.active_spring = {"spring_id": "SPR-1", "status": "active"}
    fake_service.specs = [
        {
            "spec_id": "SPEC-001",
            "status": "in_progress",
            "task_context": {"session_id": "S-1", "task_id": "T-1"},
        },
        {
            "spec_id": "SPEC-002",
            "status": "completed",
            "task_context": {"session_id": "S-1", "task_id": "T-2"},
        },
        {
            "spec_id": "SPEC-003",
            "status": "in_progress",
            "task_context": {"session_id": "OTRA", "task_id": "T-3"},
        },
    ]
    fake_service.stamp = {}

    out = session_handlers.session_close(_ctx(tmp_path), {"session_id": "S-1"})

    assert out.ok
    assert out.data["flow"] == "tools"
    assert [s["spec_id"] for s in out.data["orphan_specs"]] == ["SPEC-001"]
    assert fake_service.stamp["flow"] == "tools", "el sello declara cómo se ejecutó"


# ── costo por sesión (F5.4) ──────────────────────────────────────────────


def test_el_cierre_persiste_costo_y_tokens(tmp_path, fake_service):
    out = session_handlers.session_close(
        _ctx(tmp_path),
        {
            "session_id": "S-1",
            "cost_usd": 0.42,
            "tokens": {"input": 100, "output": 20, "cacheRead": 900},
        },
    )
    assert out.ok
    assert fake_service.cost_usd == 0.42
    assert fake_service.tokens["cacheRead"] == 900
    assert out.data["session"]["tokens"]["input"] == 100


def test_costo_basura_no_se_convierte_en_cero(tmp_path, fake_service):
    """Un costo desconocido es None, nunca 0: 0 significa 'fue gratis'."""
    out = session_handlers.session_close(
        _ctx(tmp_path), {"session_id": "S-1", "cost_usd": "no-es-un-numero"}
    )
    assert out.ok
    assert fake_service.cost_usd is None


def test_costo_no_finito_se_descarta(tmp_path, fake_service):
    out = session_handlers.session_close(
        _ctx(tmp_path), {"session_id": "S-1", "cost_usd": float("nan")}
    )
    assert out.ok
    assert fake_service.cost_usd is None


# ── dominio ──────────────────────────────────────────────────────────────


def test_close_session_guarda_el_sello_en_la_entidad():
    from fast_mcp.features.delta_tracking.domain.entities import Session

    repo = _FakeRepo()
    repo.sessions["S-9"] = Session(session_id="S-9", project_id="p")
    service = SprintService(project_root="/tmp", repo=repo)

    record = service.close_session(
        "S-9",
        outcome="succeeded",
        stamp={"kind": "tests", "verified": True},
        cost_usd=1.25,
        tokens={"input": 10, "cacheRead": 90},
    )

    assert record["stamp"]["kind"] == "tests"
    assert record["stamp"]["verified"] is True
    assert record["cost_usd"] == 1.25
    assert record["tokens"]["cacheRead"] == 90
    assert repo.sessions["S-9"].status == "closed"
    # El sello y el costo sobreviven a la serialización (lo que lee
    # `session_context` y la jerarquía de `session_list`).
    again = Session.from_dict(record)
    assert again.to_dict()["stamp"]["kind"] == "tests"
    assert again.to_dict()["cost_usd"] == 1.25


def test_sin_sello_el_registro_no_inventa_uno():
    from fast_mcp.features.delta_tracking.domain.entities import Session

    repo = _FakeRepo()
    repo.sessions["S-9"] = Session(session_id="S-9", project_id="p")
    service = SprintService(project_root="/tmp", repo=repo)

    record = service.close_session("S-9", outcome="failed")

    assert record["stamp"] == {}, "el dominio no fabrica un sello: lo normaliza el handler"


# ── re-sellado: una verificación posterior al cierre actualiza el sello ──


class _StampRepo:
    def __init__(self, session):
        self.session = session

    def get_session(self, session_id):
        return self.session if session_id == self.session.session_id else None

    def save_session(self, session):
        self.session = session


def _session_entity(status="active", stamp=None):
    from fast_mcp.features.delta_tracking.domain.entities import Session

    return Session(session_id="S-1", spring_id="SPR-1", status=status, stamp=stamp)


def test_stamp_session_actualiza_una_sesion_activa():
    svc = SprintService(
        repo=_StampRepo(_session_entity(stamp={"kind": "none", "at": "2026-11-20T00:00:00Z"}))
    )

    out = svc.stamp_session(
        "S-1", {"kind": "tests", "verified": True, "at": "2026-11-20T01:00:00Z"}
    )

    assert out["stamp"]["kind"] == "tests"
    assert out["stamp"]["verified"] is True


def test_stamp_session_no_toca_una_sesion_cerrada():
    svc = SprintService(repo=_StampRepo(_session_entity(status="closed")))

    assert svc.stamp_session("S-1", {"kind": "tests", "at": "2026-11-20T01:00:00Z"}) is None


def test_stamp_session_no_retrocede_en_el_tiempo():
    svc = SprintService(
        repo=_StampRepo(_session_entity(stamp={"kind": "tests", "at": "2026-11-20T02:00:00Z"}))
    )

    assert svc.stamp_session("S-1", {"kind": "none", "at": "2026-11-20T01:00:00Z"}) is None
    assert svc._repo.session.stamp["kind"] == "tests"


def test_record_verification_resella_la_sesion_activa(monkeypatch):
    from fast_mcp.features.tasks.handlers import verification as verification_handlers

    class _Svc:
        def __init__(self):
            self.calls: list[dict] = []

        def get_session(self, sid):
            return {"session_id": sid, "stamp": {"rounds": 2, "kind": "none"}}

        def get_session_plan(self, sid):
            return {"acceptance": ["a", "b"]}

        def stamp_session(self, session_id, stamp):
            self.calls.append(stamp)
            return {"session_id": session_id}

    svc = _Svc()
    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda chain_dir: svc)
    monkeypatch.setattr(_di, "push_trace", lambda chain, ev: True)

    verification_handlers.record_verification(
        _ctx("/tmp/proj"),
        session_id="S-1",
        passed=True,
        level="tool",
        runner="pytest -q",
        returncode=0,
    )

    assert svc.calls, "la verificación tiene que re-sellar la sesión"
    stamp = svc.calls[0]
    assert stamp["kind"] == "tests"
    assert stamp["verified"] is True
    assert stamp["source"] == "task_tool"
    assert stamp["criteria"] == 2
    assert stamp["rounds"] == 2


def test_record_verification_rojo_resella_a_none(monkeypatch):
    from fast_mcp.features.tasks.handlers import verification as verification_handlers

    class _Svc:
        def __init__(self):
            self.calls: list[dict] = []

        def get_session(self, sid):
            return {"session_id": sid, "stamp": {"rounds": 0}}

        def get_session_plan(self, sid):
            return {}

        def stamp_session(self, session_id, stamp):
            self.calls.append(stamp)
            return {}

    svc = _Svc()
    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda chain_dir: svc)
    monkeypatch.setattr(_di, "push_trace", lambda chain, ev: True)

    verification_handlers.record_verification(
        _ctx("/tmp/proj"),
        session_id="S-1",
        passed=False,
        level="tool",
        runner="pytest -q",
        returncode=1,
    )

    assert svc.calls[0]["kind"] == "none"
    assert svc.calls[0]["verified"] is False


# ── el cierre no borra evidencia: `none` no pisa un verde sin dato nuevo ──


def test_el_cierre_no_pisa_un_verde_con_un_none_ciego():
    svc = SprintService(
        repo=_StampRepo(
            _session_entity(
                status="active",
                stamp={"kind": "tests", "verified": True, "at": "2026-11-21T00:02:05"},
            )
        )
    )

    out = svc.close_session(
        "S-1",
        outcome="succeeded",
        stamp={"kind": "none", "verified": False, "verified_at": "", "at": "2026-11-21T00:10:00"},
    )

    assert out["stamp"]["verified"] is True
    assert out["stamp"]["kind"] == "tests"


def test_el_cierre_acepta_el_none_cuando_hubo_verde_y_despues_escritura():
    svc = SprintService(
        repo=_StampRepo(
            _session_entity(
                status="active",
                stamp={"kind": "tests", "verified": True, "at": "2026-11-21T00:02:05"},
            )
        )
    )

    out = svc.close_session(
        "S-1",
        outcome="succeeded",
        stamp={
            "kind": "none",
            "verified": False,
            # El plugin SÍ vio un verde (posterior al guardado) y aun así
            # cierra en none: escribió después. El downgrade es correcto.
            "verified_at": "2026-11-21T00:05:00",
            "at": "2026-11-21T00:10:00",
        },
    )

    assert out["stamp"]["verified"] is False
    assert out["stamp"]["kind"] == "none"
