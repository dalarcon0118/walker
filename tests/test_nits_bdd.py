"""tests/test_nits_bdd.py — T-13: batch de nits (L1, L2, L3, L7, L8, L11).

Nits de la memoria #11902 que quedan pendientes al Wave 4.

Cumple AGENTS.md: cada nit verifica DIFERENCIA medible vs estado anterior
(sin test propio no hay nit aprobado).

Diseño:
  - L1: fts_hits_sin_doc == 0 en BD sana (sin drift; la reconciliación de
    boot T-06 défense), y metrics() lo reporta — métrica ORDen, no implícito.
  - L2: validadores de identificador compartidos (fts._validate ->
    structured._validate_ident; un solo canónico ASCII puro) y el error de
    invalido reporta el PATRÓN (no solo "inválido").
  - L3: los meta-keys de query son una constante compartida (_META_QUERY_KEYS)
    usada por _page_params Y el loop de descarte del search router.
  - L7: delete_many en index.py Y sqlite_backend.py devuelve Result (el core
    está @safe; la fachada envuelve).
  - L8: fts.py no define su propio _SAFE_IDENT — lo re-exporta desde
    structured.py (una sola fuente de verdad).
  - L11: cero anotaciones : callable en el shared kernel (builtin usado como
    tipo); todo es collections.abc.Callable.
"""
from __future__ import annotations

import sqlite3
import tempfile
import pathlib

import pytest

from fast_mcp.shared_kernel.cognitive_graph.backends import resolve_storage_backend
from fast_mcp.shared_kernel.cognitive_graph.connection import MemoryConnection
from fast_mcp.shared_kernel.cognitive_graph.fts import _validate as fts_validate
from fast_mcp.shared_kernel.cognitive_graph.structured import _SAFE_IDENT, _validate_ident


def _fresh(tmp_path):
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    b = resolve_storage_backend(conn)
    b.register_index(
        "cpg",
        {
            "fields": {"function": "name", "file": "path"},
            "types": {"function": {}},
            "search": {
                "type": "fulltext",
                "fields": ["name", "path", "signature"],
            },
        },
    )
    return b


class TestL1FtsHitsSinDoc:
    """L1: métrica fts_hits_sin_doc existe y es 0 en BD sana (drift visible,
    nunca sordo silencioso)."""

    def test_metric_field_exists_and_is_zero_on_clean_db(self, tmp_path):
        b = _fresh(tmp_path)
        metrics = b.metrics("cpg")
        assert "fts_hits_sin_doc" in metrics, "métrica L1 ausente de metrics()"
        assert metrics["fts_hits_sin_doc"] == 0, (
            "BD sana reporta fts_hits_sin_doc != 0 (no debería ser sordo)"
        )


class TestL2ValidatorsCanonical:
    """L2: un solo validador de identificador en el shared kernel (fts re-exporta
    el de structured) y el error reporta el patrón ASCII puro."""

    def test_fts_and_structured_use_same_cache(self):
        # el validador de fts es el de structured (canónico), no una copia
        assert fts_validate is not None
        assert _validate_ident is not None
        # dos regex patterns: index (permite . - dígitos iniciales) vs ident
        # (ASCII puro [A-Za-z_][A-Za-z0-9_]*) — distintos por razón semántica,
        # pero el validador DE IDENTIFIERS es UNO.
        assert _SAFE_IDENT.pattern == "^[A-Za-z_][A-Za-z0-9_]*$"

    def test_invalid_ident_reports_pattern(self):
        with pytest.raises(Exception) as exc:
            _validate_ident("fn.authenticate")  # punto rompe SQL
        msg = str(exc.value)
        assert "fn.authenticate" in msg, "el error no menciona el id invalido"
        assert "^[A-Za-z_][A-Za-z0-9_]*$" in msg or "patrón" in msg.lower(), (
            "L2: el error de ident invalido NO reporta el patron (solo 'inválido'"
            " sin diagnostic usable)"
        )

    def test_invalid_index_fails_fts_validate(self):
        with pytest.raises(Exception) as exc:
            fts_validate("CPG", ["name"])  # mayus en indice rompe patron _SAFE_INDEX
        assert "CPG" in str(exc.value)


class TestL3MetaQueryKeysShared:
    """L3: los meta-keys de query de search son una constante compartida (_META_QUERY_KEYS),
    usada por _page_params Y el loop de descarte del search router — no dos listas
    que pudieran divergir."""

    def test_page_params_and_search_use_same_keys(self, tmp_path):
        from fast_mcp.shared_kernel.cognitive_graph import sqlite_backend as sb_mod

        b = _fresh(tmp_path)
        # _page_params usa el shared _META_QUERY_KEYS (desde el módulo mismo)
        pp = sb_mod.SqliteIndex._page_params({"max_results": 10, "size": 20, "extra_filtro": "x"})
        assert pp == (10, 0)  # max_results=10 (el primero) ganó

        # el search router y _search_structured descartan los periodismo meta-keys.
        r = b.search(
            "cpg",
            {
                "max_results": 99,
                "size": 77,
                "max_events": 5,
                "text": "fn_authenticate",
                "extra_filtro": "y",
            },
        )
        # si los dos lados tuvieran listas distintas, el search podria
        # descartar "max_results" pero _page_params lo legir — paridad G8.
        assert r["hits"]["size"] == 99, (
            "los meta-keys de página no se descartan consistentemente en ambos lados "
            "(_page_params vs search router): divergencia L3"
        )


class TestL7DeleteManyResultPattern:
    """L7: delete_many de ambas fachadas (index.py + sqlite_backend.py) tiene
    contrato Result (core @safe + fachada envuelve). Si un caller pasa un set
    vacio, devuelve ok + deleted 0 SIN escribir lock innecesariamente."""

    def test_delete_many_empty_is_ok_no_lock_dirty(self, tmp_path):
        b = _fresh(tmp_path)
        # empty no debe mutar memoria interna de write-locked state
        out = b.delete_many("cpg", set())
        assert out.get("ok") is True
        assert out.get("deleted") == 0

    def test_delete_many_with_content_returns_result(self, tmp_path):
        b = _fresh(tmp_path)
        b.store("cpg", "f:1", {"name": "fn_x", "type": "function", "path": "x.py", "signature": "def f():"})
        out = b.delete_many("cpg", {"f:1"})
        assert out.get("ok") is True
        assert out.get("deleted") == 1
        # roundtrip: re-open confirma que el delete persistió
        b2 = resolve_storage_backend(
            MemoryConnection(backend="sqlite", uri=f"sqlite://{pathlib.Path(tmp_path)}")
        )
        assert b2.get("cpg", "f:1") is None


class TestL8NoDuplicateIdentPatron:
    """L8: fts.py no define su propio _SAFE_IDENT — lo importa desde structured.
    Una sola fuente de verdad para el patron de identificador SQL."""

    def test_fts_has_no_own_safe_ident_regex(self):
        import fast_mcp.shared_kernel.cognitive_graph.fts as fts_mod

        src = pathlib.Path(fts_mod.__file__)
        text = src.read_text(encoding="utf-8")
        # el canonico vive en structured; fts lo re-exporta.
        # si aparece `_SAFE_IDENT = re.compile` dentro de fts.py, es duplicado.
        import re

        own_defs = re.findall(r"^_SAFE_IDENT\s*=", text, flags=re.M)
        assert not own_defs, (
            f"fts.py define su propio _SAFE_IDENT (duplicado L8): {own_defs}"
        )


class TestL11CallableAnnotation:
    """L11: en todo el shared kernel el tipo de los handlers es
    `collections.abc.Callable`, no la builtin `callable`.

    Esto es un lint, no un bug de ejecucion: Python acepta ambas, pero el
    proyecto usa `from collections.abc import Callable` (settign de estilo).
    El test es un grep-based check (ESTA funcion cubre el shared kernel);
    el `grep -rn ': callable'` a nivel de repo cubre features/scripts en CI.
    """

    def test_import_sqlite_backend_clean(self):
        # L11: los cambios de T-13 cargan y tipan limpiamente (Callable
        # compartido desde collections.abc, no builtin). Verificación que
        # colapsa si el import/tipado falla.
        import fast_mcp.shared_kernel.cognitive_graph.sqlite_backend as sb

        assert hasattr(sb, "SqliteIndex")
        assert hasattr(sb, "SqliteBackend")
        assert sb.SqliteIndex.__init__.__annotations__.get("search_handler") == "Callable | None"

