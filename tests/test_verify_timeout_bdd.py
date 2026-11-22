"""test_verify_timeout_bdd.py — F3b: la verificación no se autodestruye.

Bug medido (2026-11-21): el predicado auto-derivado `python3 -m pytest -q`
salía ROJO por `timeout (30s)` mientras la suite pasaba en 35,2 s. El 30 s fijo
convertía el cierre en imposible por construcción: el gate pedía un verde que
el propio verificador no podía producir.

Contrato:
  - `verify_timeout_s()` es configurable (`WALKER_VERIFY_TIMEOUT`) y por
    defecto cubre la suite (300 s, igual que `run_tests`).
  - Un comando más lento que el timeout falla con el valor REAL, no con "30s".
"""

from __future__ import annotations

import pytest

from fast_mcp.features.tasks.infrastructure.verify_engine import (
    DEFAULT_VERIFY_TIMEOUT_S,
    _verify_command_exit,
    verify_timeout_s,
)


def test_default_cubre_la_suite(monkeypatch):
    monkeypatch.delenv("WALKER_VERIFY_TIMEOUT", raising=False)
    assert DEFAULT_VERIFY_TIMEOUT_S >= 60, "30s no alcanza para la suite (35s)"
    assert verify_timeout_s() == DEFAULT_VERIFY_TIMEOUT_S


def test_env_configura_el_timeout(monkeypatch):
    monkeypatch.setenv("WALKER_VERIFY_TIMEOUT", "45")
    assert verify_timeout_s() == 45


@pytest.mark.parametrize("raw", ["", "abc", "0", "-3", "  "])
def test_valor_invalido_cae_al_default(monkeypatch, raw):
    monkeypatch.setenv("WALKER_VERIFY_TIMEOUT", raw)
    assert verify_timeout_s() == DEFAULT_VERIFY_TIMEOUT_S


def test_timeout_reporta_el_valor_real(monkeypatch, tmp_path):
    monkeypatch.setenv("WALKER_VERIFY_TIMEOUT", "1")
    passed, info = _verify_command_exit(
        {"command": "python3 -c 'import time; time.sleep(1.5)'"}, str(tmp_path)
    )
    assert passed is False
    assert info["error"] == "timeout (1s)"


def test_comando_rapido_sigue_verde(monkeypatch, tmp_path):
    monkeypatch.setenv("WALKER_VERIFY_TIMEOUT", "30")
    passed, info = _verify_command_exit({"command": "true"}, str(tmp_path))
    assert passed is True
    assert info["exit_code"] == 0
