"""Tests BDD (pytest-bdd) — Repositorio InMemory de Sesiones MCP (F8).

Feature value: persistencia en memoria de McpSession con return pattern,
lifecycle completo (save/load/close) y soporte multi-sesión.

Escenarios: bdd/features/protocol_session_store.feature
"""
from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/protocol_session_store.feature")


@pytest.fixture
def session_ctx() -> dict:
    return {}


# ── Helpers ───────────────────────────────────────────────────────


def _make_session():
    from features.main.cli_interface.domain.entities import McpSession
    return McpSession.create()


# ── Given ─────────────────────────────────────────────────────────


@given("un InMemorySessionRepository vacío")
def _empty_repo(session_ctx: dict):
    from features.main.cli_interface.infrastructure.session_store import (
        InMemorySessionRepository,
    )
    session_ctx["repo"] = InMemorySessionRepository()


@given("una McpSession creada")
def _session_created(session_ctx: dict):
    session_ctx["session"], _ = _make_session()


@given("una McpSession inicializada")
def _session_initialized(session_ctx: dict):
    session, _ = _make_session()
    session.initialize("test-client")
    session_ctx["session"] = session


@given("un InMemorySessionRepository con una sesión guardada")
def _repo_with_session(session_ctx: dict):
    from features.main.cli_interface.infrastructure.session_store import (
        InMemorySessionRepository,
    )
    repo = InMemorySessionRepository()
    session, _ = _make_session()
    session.initialize("test-client")
    repo.save(session)
    session_ctx["repo"] = repo
    session_ctx["session"] = session


@given("dos McpSessions creadas")
def _two_sessions(session_ctx: dict):
    s1, _ = _make_session()
    s2, _ = _make_session()
    session_ctx["s1"] = s1
    session_ctx["s2"] = s2


# ── When ──────────────────────────────────────────────────────────


@when("guardo la sesión")
def _save_session(session_ctx: dict):
    session_ctx["save_result"] = session_ctx["repo"].save(session_ctx["session"])


@when("la cargo por su id")
def _load_session(session_ctx: dict):
    repo = session_ctx["repo"]
    sid = session_ctx["session"].session_id.value
    session_ctx["load_result"] = repo.load(sid)


@when("cargo una sesión inexistente")
def _load_missing(session_ctx: dict):
    session_ctx["load_result"] = session_ctx["repo"].load("no-existe")


@when("cierro la sesión por su id")
def _close_session(session_ctx: dict):
    repo = session_ctx["repo"]
    sid = session_ctx["session"].session_id.value
    session_ctx["close_result"] = repo.close(sid)


@when("la cierro")
def _close_session_short(session_ctx: dict):
    repo = session_ctx["repo"]
    sid = session_ctx["session"].session_id.value
    session_ctx["close_result"] = repo.close(sid)


@when("cierro una sesión inexistente")
def _close_missing(session_ctx: dict):
    session_ctx["close_result"] = session_ctx["repo"].close("no-existe")


@when("la cargo")
def _load_session_again(session_ctx: dict):
    repo = session_ctx["repo"]
    sid = session_ctx["session"].session_id.value
    session_ctx["load_result"] = repo.load(sid)


@when("la activo")
def _activate_session(session_ctx: dict):
    session_ctx["session"].activate()


@when("guardo la sesión nuevamente")
def _save_again(session_ctx: dict):
    session_ctx["repo"].save(session_ctx["session"])


@when("la guardo nuevamente")
def _save_again_short(session_ctx: dict):
    session_ctx["repo"].save(session_ctx["session"])


@when("guardo ambas sesiones")
def _save_both(session_ctx: dict):
    repo = session_ctx["repo"]
    repo.save(session_ctx["s1"])
    repo.save(session_ctx["s2"])


# ── Then ──────────────────────────────────────────────────────────


@then("la sesión cargada coincide con la original")
def _loaded_matches(session_ctx: dict):
    loaded, errors = session_ctx["load_result"]
    original = session_ctx["session"]
    assert loaded is not None and not errors
    assert loaded.session_id.value == original.session_id.value


@then("la carga retorna None")
def _load_returns_none(session_ctx: dict):
    loaded, errors = session_ctx["load_result"]
    assert loaded is None and errors


@then("el error indica not found")
def _error_not_found(session_ctx: dict):
    _, errors = session_ctx["load_result"]
    assert any("not found" in e for e in errors), f"Esperaba 'not found' en {errors}"


@then("el repositorio queda vacío")
def _repo_empty(session_ctx: dict):
    assert session_ctx["repo"].count() == 0


@then("la operación falla con errores")
def _close_fails(session_ctx: dict):
    closed, errors = session_ctx["close_result"]
    assert not closed and errors


@then("el repositorio tiene 2 sesiones")
def _repo_count(session_ctx: dict):
    assert session_ctx["repo"].count() == 2


@then("list_sessions retorna 2 sesiones")
def _list_count(session_ctx: dict):
    assert len(session_ctx["repo"].list_sessions()) == 2
