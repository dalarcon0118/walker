"""Fixtures globales de la suite de tests.

Seguridad: fuerza SessionStore.SESSIONS_DIR a un directorio temporal por
test para que NINGUNA prueba toque el <cognitive>/sessions/ real.

Motivación (incidente real): cuando un test ejercita la cadena pública
(use_cases -> WorkflowAPI.ensure_session -> SessionStore.gc_stale) con
SESSIONS_DIR en None, la resolución via config apunta al dir real del
proyecto y un GC de sesiones stale puede borrar datos productivos.
Este fixture hace que el default de toda la suite sea un tamptdir aislado.
"""
from __future__ import annotations

import pytest

from features.engine.domain.session import SessionStore


@pytest.fixture(autouse=True)
def _sandbox_sessions_dir(tmp_path, monkeypatch):
    """Apunta SessionStore a un sessions_dir temporal por cada test."""
    sdir = tmp_path / "walkertest-sessions"
    sdir.mkdir(exist_ok=True)
    monkeypatch.setattr(SessionStore, "SESSIONS_DIR", str(sdir))
    monkeypatch.setattr(SessionStore, "ACTIVE_FILE", None)
    monkeypatch.setattr(SessionStore, "_LAST_GC", 0.0)
    yield
    # Restaura el estado de clase para no filtrar tmp_path entre tests.
    SessionStore.SESSIONS_DIR = None
    SessionStore.ACTIVE_FILE = None
    SessionStore._LAST_GC = 0.0