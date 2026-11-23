"""tests/test_verify_engine_spago_bdd.py — runner de tests para PureScript.

Bug medido 2026-11-22 en client-chat: `run_tests` fallaba con "el proyecto no
declara coding.test_framework" y, aun declarándolo, `_normalize_test_framework`
no conocía `spago` ni `purescript` (PureScript era invisible para la
verificación). El remedio no es inventar pytest: es declarar el runner.

Contrato:
  - `spago` y `purescript` normalizan a la key `spago`.
  - El comando base es `npx spago test` (resuelve el binario local).
  - `spago` no hereda el flag `-k` de pytest al filtrar por nombre.
  - `detect_tooling` reconoce `spago.yaml` como evidencia del runner.
"""

from __future__ import annotations

import json

from fast_mcp.features.tasks.infrastructure import verify_engine as ve
from fast_mcp.shared_kernel.project_tooling import detect_tooling


def _manifest(tmp_path, framework: str) -> str:
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "project.json").write_text(
        json.dumps({"project": {"coding": {"test_framework": framework}}})
    )
    return str(tmp_path)


def test_normaliza_spago():
    assert ve._normalize_test_framework("spago") == "spago"
    assert ve._normalize_test_framework("Spago test") == "spago"


def test_normaliza_purescript_como_alias_de_spago():
    assert ve._normalize_test_framework("purescript") == "spago"
    assert ve._normalize_test_framework("PureScript (Spago)") == "spago"


def test_build_test_command_spago(tmp_path):
    root = _manifest(tmp_path, "spago")

    assert ve.build_test_command(root) == ["npx", "spago", "test"]


def test_spago_no_hereda_el_flag_de_pytest():
    assert ve._TEST_NAME_FLAG["spago"] == []


def test_detect_tooling_reconoce_spago_yaml(tmp_path):
    (tmp_path / "spago.yaml").write_text("package:\n  name: demo\n")

    out = detect_tooling(tmp_path, "javascript")

    assert out.get("test_framework") == "spago"


def test_detect_tooling_no_pisa_un_runner_mas_especifico(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"devDependencies": {"vitest": "1"}}))

    out = detect_tooling(tmp_path, "javascript")

    assert out.get("test_framework") == "vitest"
