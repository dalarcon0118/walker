"""test_index_generation_retry_bdd.py — el efecto `index_code` no muere al primer choque.

Medido 2026-11-26: 6 `side_effect_failed` de `index_code` en el workflow, todos
con el mismo error ("el storage cambió bajo el cache", generación X != committed)
y el CPG quedó `stale`. Causa: un único reintento no alcanza cuando el watcher y
otra sesión escriben el mismo índice; el efecto es `optional: true`, así que el
paso seguía y nadie reintentaba.

Contrato:
1. Un conflicto de generación se reintenta con cache refrescada, hasta el
   presupuesto (`WALKER_INDEX_GENERATION_RETRIES`, default 3).
2. Si el conflicto persiste, se devuelve `ok: False` (nunca escritura ciega ni
   éxito fingido).
3. Un error que NO es conflicto de generación no se reintenta.
"""

from __future__ import annotations

import pytest

from fast_mcp.shared_kernel.cognitive_graph import sqlite_backend as sb


@pytest.fixture(autouse=True)
def _sin_espera(monkeypatch):
    monkeypatch.setattr(sb.time, "sleep", lambda _s: None)


def _index():
    idx = sb.SqliteIndex.__new__(sb.SqliteIndex)
    idx.name = "cpg"
    idx._generation_retries = 0
    idx.reloads = 0

    def _reload(_keep):
        idx.reloads += 1

    idx._reload_light_state = _reload
    return idx


def _conflict():
    return {"ok": False, "error": "SqliteIndex['cpg']: el storage " + sb._GENERATION_CONFLICT}


def test_reintenta_hasta_que_el_conflicto_cede():
    idx = _index()
    calls = {"n": 0}

    def op():
        calls["n"] += 1
        return _conflict() if calls["n"] <= 2 else {"ok": True}

    result = idx._with_generation_retry(set(), op)

    assert result == {"ok": True}
    assert calls["n"] == 3, "dos conflictos + el intento que gana"
    assert idx.reloads == 2, "cada reintento refresca el cache"
    assert idx._generation_retries == 2


def test_si_el_conflicto_persiste_devuelve_fallo():
    idx = _index()

    def op():
        return _conflict()

    result = idx._with_generation_retry(set(), op)

    assert result.get("ok") is False
    assert sb._GENERATION_CONFLICT in result["error"]
    assert idx._generation_retries == sb._GENERATION_RETRIES


def test_un_error_que_no_es_conflicto_no_se_reintenta():
    idx = _index()
    calls = {"n": 0}

    def op():
        calls["n"] += 1
        return {"ok": False, "error": "disk full"}

    result = idx._with_generation_retry(set(), op)

    assert result["error"] == "disk full"
    assert calls["n"] == 1
    assert idx.reloads == 0
