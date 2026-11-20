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
