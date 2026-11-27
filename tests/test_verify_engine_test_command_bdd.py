"""tests/test_verify_engine_test_command_bdd.py — el gate lo declara el proyecto.

Bug medido 2026-11-26 en backend-microservices: `run_tests` exigía
`coding.test_framework` y solo conocía runners de una lista fija (pytest, jest,
spago...). Django (`python manage.py test`) no estaba y el manifiesto no podía
declararlo sin mentir con un framework ajeno; `VERIFY.QA_RUN` era insatisfacible
aunque el proyecto tuviera un gate real y verde.

Contrato:
  - `coding.test_command` se ejecuta literal (shlex), sin anexarle filtros.
  - `resolve_test_framework` devuelve `custom` cuando hay comando declarado.
  - Sin comando se conserva el camino anterior (framework conocido o fail-loud).
"""

from __future__ import annotations

import json

import pytest

from fast_mcp.features.tasks.infrastructure import verify_engine as ve


def _manifest(tmp_path, coding: dict) -> str:
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "project.json").write_text(
        json.dumps({"project": {"coding": coding}})
    )
    return str(tmp_path)


def test_test_command_declarado_se_ejecuta_literal(tmp_path):
    root = _manifest(
        tmp_path,
        {"test_command": ".venv/bin/python manage.py test tests.accounting"},
    )

    assert ve.resolve_test_framework(root) == "custom"
    assert ve.build_test_command(root) == [
        ".venv/bin/python", "manage.py", "test", "tests.accounting",
    ]


def test_custom_no_hereda_target_ni_flag_de_nombre(tmp_path):
    root = _manifest(tmp_path, {"test_command": "./run-gate.sh"})

    assert ve.build_test_command(root, target="tests/x.py", name="test_x") == [
        "./run-gate.sh"
    ]


def test_sin_declaracion_sigue_siendo_fail_loud(tmp_path):
    root = _manifest(tmp_path, {})

    with pytest.raises(ve.TestRunnerUnavailable):
        ve.resolve_test_framework(root)
