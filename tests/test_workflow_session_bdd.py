"""Tests BDD (pytest-bdd) — Reuso de sesión por chain_dir (WorkflowAPI).

Feature value: el API de workflow reutiliza la sesión de un chain_dir en
lugar de crear una nueva por cada llamada CLI/MCP (evita llenar
<cognitive>/sessions/ con duplicados). Solo crea cuando no hay sesión
reutilizable para el chain_dir.

Escenarios: bdd/features/workflow_session.feature
"""
from __future__ import annotations

import os

import pytest
from pytest_bdd import given, scenarios, then, when

from core.engine.domain.session import SessionRecord, SessionStore
from core.engine.application.workflow_api import WorkflowAPI

scenarios("bdd/features/workflow_session.feature")


@pytest.fixture
def wf_ctx() -> dict:
    return {}


# ── Helpers ───────────────────────────────────────────────────────


def _install_sessions_dir(tmp_path) -> None:
    """Apunta SessionStore a un sessions_dir temporal (monkeypatch via attr)."""
    sdir = str(tmp_path / "sessions")
    os.makedirs(sdir, exist_ok=True)
    SessionStore.SESSIONS_DIR = sdir
    SessionStore.ACTIVE_FILE = None


# ── Given ─────────────────────────────────────────────────────────


@given("un sessions_dir temporal vacío")
def _tmp_sessions_dir(wf_ctx: dict, tmp_path):
    _install_sessions_dir(tmp_path)
    wf_ctx["sessions_dir"] = str(tmp_path / "sessions")


@given("un sessions_dir temporal con una sesión stale")
def _tmp_sessions_stale(wf_ctx: dict, tmp_path):
    _install_sessions_dir(tmp_path)
    wf_ctx["sessions_dir"] = str(tmp_path / "sessions")
    from datetime import datetime, timedelta, timezone

    stale = SessionRecord.create(chain_dir="/proj/data/spring/sprint-stale")
    stale.last_read_at = (
        datetime.now(timezone.utc) - timedelta(hours=48)
    ).isoformat()
    stale.updated_at = stale.last_read_at
    SessionStore.save(stale)
    wf_ctx["stale_id"] = stale.session_id


@given("una sesión activa reciente")
def _active_recent(wf_ctx: dict):
    active = SessionRecord.create(chain_dir="/proj/data/spring/sprint-active")
    SessionStore.save(active)
    SessionStore.set_active(active.session_id)
    wf_ctx["active_id"] = active.session_id


@given("un WorkflowAPI con chain_dir explícito")
def _api_with_chain(wf_ctx: dict):
    wf_ctx["chain_dir"] = "/proj/data/spring/sprint-001"
    wf_ctx["api"] = WorkflowAPI(chain_dir=wf_ctx["chain_dir"], project_id="test-proj")


# ── When ──────────────────────────────────────────────────────────


@when("aseguro sesión")
def _ensure(wf_ctx: dict):
    api = wf_ctx["api"]
    wf_ctx["session_id"] = api.ensure_session()
    wf_ctx["api"] = api


@when("un nuevo WorkflowAPI con el mismo chain_dir asegura sesión")
def _ensure_same_chain(wf_ctx: dict):
    api = WorkflowAPI(chain_dir=wf_ctx["chain_dir"], project_id="test-proj")
    wf_ctx["session_id_2"] = api.ensure_session()
    wf_ctx["api"] = api


@when("un nuevo WorkflowAPI con otro chain_dir asegura sesión")
def _ensure_other_chain(wf_ctx: dict):
    api = WorkflowAPI(chain_dir="/proj/data/spring/sprint-002", project_id="test-proj")
    wf_ctx["session_id_2"] = api.ensure_session()
    wf_ctx["api"] = api


@when("el GC de sesiones corre")
def _gc_runs(wf_ctx: dict):
    # min_sessions=1 y max_hours=48: en escenarios de prueba se anulan
    # los defaults de seguridad (GC_STALE_MIN_SESSIONS=50, max_hours=168)
    # para ejercitar la lógica con un umbral controlado.
    wf_ctx["gc_removed"] = SessionStore.gc_stale(max_hours=48, min_sessions=1)


# ── Then ──────────────────────────────────────────────────────────


@then("el sessions_dir tiene 1 sesión")
def _one_session(wf_ctx: dict):
    sessions = SessionStore.list_sessions()
    assert len(sessions) == 1, f"Esperaba 1 sesión, hay {len(sessions)}"


@then("el sessions_dir tiene 2 sesiones")
def _two_sessions(wf_ctx: dict):
    sessions = SessionStore.list_sessions()
    assert len(sessions) == 2, f"Esperaba 2 sesiones, hay {len(sessions)}"


@then("la sesión apunta al chain_dir dado")
def _session_chain(wf_ctx: dict):
    record = SessionStore.load(wf_ctx["session_id"])
    assert record is not None
    assert record.chain_dir == wf_ctx["chain_dir"]


@then("la sesión queda como activa")
def _active_session(wf_ctx: dict):
    active = SessionStore.get_active()
    assert active is not None
    assert active.session_id == wf_ctx["session_id"]


@then("la sesión activa es la misma")
def _same_active(wf_ctx: dict):
    active = SessionStore.get_active()
    assert active is not None
    assert active.session_id == wf_ctx["session_id"]
    assert wf_ctx["session_id_2"] == wf_ctx["session_id"]


@then("la sesión stale se elimina")
def _stale_removed(wf_ctx: dict):
    assert SessionStore.load(wf_ctx["stale_id"]) is None
    assert wf_ctx["gc_removed"] >= 1


@then("la sesión activa se conserva")
def _active_kept(wf_ctx: dict):
    assert SessionStore.load(wf_ctx["active_id"]) is not None
    active = SessionStore.get_active()
    assert active is not None
    assert active.session_id == wf_ctx["active_id"]


@pytest.fixture(autouse=True)
def _reset_session_store(tmp_path):
    """Restaura SessionStore.SESSIONS_DIR entre tests (evita contaminación)."""
    yield
    SessionStore.SESSIONS_DIR = None
    SessionStore.ACTIVE_FILE = None
    SessionStore._LAST_GC = 0.0
