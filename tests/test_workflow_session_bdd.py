"""Tests BDD (pytest-bdd) — Reuso de sesión por chain_dir (WorkflowAPI).

Feature value: el API de workflow reutiliza la sesión de un chain_dir en
lugar de crear una nueva por cada llamada CLI/MCP (evita llenar
<cognitive>/sessions/ con duplicados). Solo crea cuando no hay sesión
reutilizable para el chain_dir.

Escenarios: tests/features/workflow_session.feature
"""
from __future__ import annotations

import os

import pytest
from pytest_bdd import given, scenarios, then, when

from features.engine.domain.session import SessionRecord, SessionStore
from features.engine.application.workflow_api import WorkflowAPI

scenarios("features/workflow_session.feature")


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


@pytest.fixture(autouse=True)
def _reset_session_store(tmp_path):
    """Restaura SessionStore.SESSIONS_DIR entre tests (evita contaminación)."""
    yield
    SessionStore.SESSIONS_DIR = None
    SessionStore.ACTIVE_FILE = None
