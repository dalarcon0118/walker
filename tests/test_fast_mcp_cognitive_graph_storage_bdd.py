"""tests/test_fast_mcp_cognitive_graph_storage_bdd.py — Storage sqlite (v2).

Comportamiento del backend sqlite (ADR-WALKER-063) como SSOT del runtime:
resolución fail-fast del db path (INV-016 — sin defaults implícitos que
escriban en el CWD).
"""

from __future__ import annotations

import pytest

from fast_mcp.shared_kernel.cognitive_graph.backends import resolve_storage_backend
from fast_mcp.shared_kernel.cognitive_graph.connection import MemoryConnection


class TestFailFastDBPathResolution:
    """INV-016: el uri de conexión se resuelve explícitamente o falla.

    Vector histórico de bases parásitas: un uri `sqlite://` con path vacío
    caía en `os.path.join("", "cognitive.sqlite3")` → cognitive.sqlite3 en
    el CWD del proceso (p.ej. la raíz del repo cuando pytest corre ahí).
    El guard convierte ese default silencioso en error accionable.
    """

    def test_empty_sqlite_uri_fails_fast(self):
        """sqlite:// con path vacío → ValueError, nunca base en el CWD."""
        conn = MemoryConnection(backend="sqlite", uri="sqlite://")
        with pytest.raises(ValueError, match="path vacío"):
            resolve_storage_backend(conn)

    def test_valid_uri_resolves_inside_given_dir(self, tmp_path):
        """Un uri anclado resuelve la db DENTRO del directorio dado."""
        conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
        backend = resolve_storage_backend(conn)
        assert str(backend._db_path) == str(tmp_path / "cognitive.sqlite3")

    def test_uri_pointing_at_file_is_honored(self, tmp_path):
        """Un uri que ya apunta a un .sqlite3 se respeta tal cual."""
        db_file = tmp_path / "custom.sqlite3"
        conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{db_file}")
        backend = resolve_storage_backend(conn)
        assert str(backend._db_path) == str(db_file)


class TestLegacyBlobPathHelpersDeprecated:
    """Los helpers de paths que describen blobs legacy .json.gz están
    DEPRECATOS (ADR-WALKER-063 CA-9): el SSOT runtime de las memorias
    (cpg/ckg/delta/trace) es sqlite v2 (cognitive.sqlite3, puerto
    StorageBackend). Los blobs .json.gz ya no se escriben ni se leen en
    runtime → DeprecationWarning; se eliminarán en una versión futura."""

    def test_get_cpg_path_warns(self, tmp_path, monkeypatch):
        import pytest as _pytest

        from fast_mcp.shared_kernel import paths

        monkeypatch.setattr(paths, "walker_root", lambda: tmp_path)
        with _pytest.warns(DeprecationWarning, match="DEPRECATO"):
            paths.get_cpg_path("demo")

    def test_get_ckg_path_warns(self, tmp_path, monkeypatch):
        import pytest as _pytest

        from fast_mcp.shared_kernel import paths

        monkeypatch.setattr(paths, "walker_root", lambda: tmp_path)
        with _pytest.warns(DeprecationWarning, match="DEPRECATO"):
            paths.get_ckg_path("demo")

    def test_get_trace_dir_y_get_trace_path_warns(self, tmp_path, monkeypatch):
        import pytest as _pytest

        from fast_mcp.shared_kernel import paths

        monkeypatch.setattr(paths, "walker_root", lambda: tmp_path)
        with _pytest.warns(DeprecationWarning, match="DEPRECATO"):
            paths.get_trace_dir("demo")
        with _pytest.warns(DeprecationWarning, match="DEPRECATO"):
            paths.get_trace_path("demo", "sprint-1")

    def test_resolve_trace_path_warns(self, tmp_path, monkeypatch):
        import pytest as _pytest

        from fast_mcp.shared_kernel import paths

        monkeypatch.setattr(paths, "walker_root", lambda: tmp_path)
        chain = tmp_path / "sprint-1"
        chain.mkdir()
        with _pytest.warns(DeprecationWarning, match="DEPRECATO"):
            # El warning dispara ANTES de la resolución; el error de
            # resolución (tmp no es un proyecto preparado) es esperado aquí.
            try:
                paths.resolve_trace_path(str(chain))
            except FileNotFoundError:
                pass
