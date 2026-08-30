"""Tests — Config de conexión de memorias (ADR-WALKER-061 F6 + ADR-063 corte).

Modelo empresarial: la conexión es CONFIGURACIÓN (env), no código.
Cambiar backend = cambiar WALKER_MEMORY_BACKEND, no tocar el composition root.

ADR-WALKER-063 (corte directo): sqlite (v2) es el ÚNICO backend runtime.
`memory.backend='file'` = config INVÁLIDA (fail-fast); FileBackend no está
registrado — solo export/migración/tests (§6.6).
"""
from __future__ import annotations

import pytest

from shared.libs.cognitive_graph.backends import (
    available_backends,
    resolve_storage_backend,
)
from shared.libs.cognitive_graph.config import (
    ENV_MEMORY_BACKEND,
    ENV_MEMORY_DB,
    ENV_MEMORY_PREFIX,
    ENV_MEMORY_URI,
    load_memory_connection,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Aísla cada test del env real."""
    for var in (ENV_MEMORY_BACKEND, ENV_MEMORY_URI, ENV_MEMORY_DB, ENV_MEMORY_PREFIX):
        monkeypatch.delenv(var, raising=False)


# ── Defaults ────────────────────────────────────────────────────────────────

def test_defaults_are_sqlite_backend():
    conn = load_memory_connection("/tmp/data")
    assert conn.backend == "sqlite"
    assert conn.uri == "sqlite:///tmp/data"
    assert conn.database == "walker"
    assert conn.collection_prefix == ""


def test_base_dir_is_resolved_absolute():
    conn = load_memory_connection("relative/path")
    assert conn.uri.startswith("sqlite:///")
    assert conn.uri.endswith("relative/path")


def test_empty_base_dir_falls_back_to_default_uri():
    conn = load_memory_connection()
    assert conn.uri == "sqlite://./data"


# ── Overrides por env ────────────────────────────────────────────────────────

def test_env_overrides_backend_db_and_prefix(monkeypatch):
    monkeypatch.setenv(ENV_MEMORY_DB, "walker-prod")
    monkeypatch.setenv(ENV_MEMORY_PREFIX, "acme_")
    conn = load_memory_connection("/tmp/d")
    assert conn.database == "walker-prod"
    assert conn.collection_prefix == "acme_"
    assert conn.index_name("ckg") == "acme_ckg"


def test_uri_override_wins_over_base_dir(monkeypatch):
    # file:// se acepta como sinónimo de ruta para sqlite (SqliteBackend los
    # trata igual); el uri override gana al base_dir.
    monkeypatch.setenv(ENV_MEMORY_URI, "file:///custom/root")
    conn = load_memory_connection("/tmp/ignored")
    assert conn.uri == "file:///custom/root"


# ── Legacy 'file': fail-fast (ADR-063 corte directo, sin fallback) ──────────

def test_legacy_file_backend_config_raises_fail_fast(monkeypatch):
    monkeypatch.setenv(ENV_MEMORY_BACKEND, "file")
    with pytest.raises(ValueError, match="ya no es válido"):
        load_memory_connection("/tmp/d")


# ── Backend remoto: fail-fast sin uri ────────────────────────────────────────

def test_remote_backend_requires_uri(monkeypatch):
    monkeypatch.setenv(ENV_MEMORY_BACKEND, "postgres")
    with pytest.raises(ValueError, match="WALKER_MEMORY_URI"):
        load_memory_connection("/tmp/d")


def test_remote_backend_with_uri_passes_through(monkeypatch):
    monkeypatch.setenv(ENV_MEMORY_BACKEND, "postgres")
    monkeypatch.setenv(ENV_MEMORY_URI, "postgresql://walker@db/prod")
    conn = load_memory_connection()  # base_dir irrelevante para remotos
    assert conn.backend == "postgres"
    assert conn.uri == "postgresql://walker@db/prod"


# ── SSOT: delega en el loader central (archivo < env) ────────────────────────

def test_reads_from_central_config_file(monkeypatch):
    """memory.* se lee vía el loader central (config/config.json)."""
    import shared.core.engine.infrastructure.config as central

    monkeypatch.setattr(
        central, "_load_config_file",
        lambda: {"memory": {"backend": "postgres", "uri": "postgresql://f"}},
    )
    conn = load_memory_connection("/tmp/d")
    assert conn.backend == "postgres"  # viene del ARCHIVO de config
    assert conn.uri == "postgresql://f"


def test_env_beats_config_file(monkeypatch):
    """Precedencia del SSOT: env var > config/config.json."""
    import shared.core.engine.infrastructure.config as central

    monkeypatch.setattr(
        central, "_load_config_file",
        lambda: {"memory": {"backend": "postgres", "uri": "postgresql://f"}},
    )
    monkeypatch.setenv(ENV_MEMORY_BACKEND, "sqlite")  # env gana al archivo
    conn = load_memory_connection("/tmp/d")
    assert conn.backend == "sqlite"
    assert conn.uri == "sqlite:///tmp/d"


# ── Resolución de backend ─────────────────────────────────────────────────────

def test_resolve_storage_backend_returns_sqlite_adapter(tmp_path):
    from shared.libs.cognitive_graph.sqlite_backend import SqliteBackend
    from shared.libs.cognitive_graph.config import load_memory_connection as lmc

    conn = lmc(str(tmp_path))
    backend = resolve_storage_backend(conn)
    assert isinstance(backend, SqliteBackend)
    # ADR-063: sqlite es el ÚNICO backend runtime (file no registrado).
    assert available_backends() == ["sqlite"]


def test_resolve_storage_backend_unknown_raises_fail_fast():
    from shared.libs.cognitive_graph.connection import MemoryConnection

    conn = MemoryConnection(backend="oracle", uri="oracle://x")
    with pytest.raises(ValueError, match="desconocido"):
        resolve_storage_backend(conn)


def test_resolve_storage_backend_legacy_file_raises_fail_fast():
    """conn con backend='file' (legacy): fail-fast, sin fallback silencioso."""
    from shared.libs.cognitive_graph.connection import MemoryConnection

    conn = MemoryConnection(backend="file", uri="file:///tmp/d")
    with pytest.raises(ValueError, match="desconocido"):
        resolve_storage_backend(conn)