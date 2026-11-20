"""test_read_pool_bdd.py — pool de procesos para lecturas (AE-INV-07).

Bajo pytest el pool está apagado (`PYTEST_CURRENT_TEST`): la suite no spawnea
intérpretes. Se prueba el contrato: el flag, y que el entry point del worker
resuelve el contexto y despacha igual que el camino in-process.
"""

from __future__ import annotations

from pathlib import Path

from fast_mcp.memories import read_pool

ROOT = Path(__file__).resolve().parents[1]


def test_enabled_respeta_flag_y_pytest(monkeypatch):
    monkeypatch.delenv("WALKER_READ_POOL", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "activo")
    assert read_pool.enabled() is False

    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    assert read_pool.enabled() is True

    monkeypatch.setenv("WALKER_READ_POOL", "0")
    assert read_pool.enabled() is False


def test_dispatch_del_worker_resuelve_contexto():
    out = read_pool.dispatch("brief", str(ROOT), {"_payload": {}})

    assert out["type"] == "brief"
    assert out["degraded"] is True
    assert out["missing"], "sin ancla ni sesión debe declarar lo que falta"
