"""
Tests BDD (pytest-bdd) — Detección de imports alucinados.

Escenarios: bdd/features/cpg_hallucination.feature

Cubre features/code_analysis/infrastructure/hallucination.py: scan_file y
check_python_import. Contrato: detecta módulos inexistentes y respeta válidos.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/cpg_hallucination.feature")


@pytest.fixture
def hal_ctx() -> dict:
    return {}


@pytest.fixture
def hal_project(tmp_path: Path, hal_ctx: dict) -> dict:
    """Crea un mini proyecto con mymod.py + test.py que importa paquete fantasma."""
    root = tmp_path
    (root / "mymod.py").write_text("def hello():\n    return 1\n")
    test = root / "test.py"
    test.write_text("from mymod import hello\nfrom nonexistent_pkg import foo\n")
    hal_ctx["root"] = str(root)
    hal_ctx["test"] = str(test)
    return hal_ctx


@given("un directorio con un módulo definido y un test que lo importa")
def _bdd_modulo_definido(hal_project: dict):
    assert os.path.isfile(os.path.join(hal_project["root"], "mymod.py"))


@given("el test importa un paquete inexistente")
def _bdd_paquete_fantasma(hal_project: dict):
    assert "nonexistent_pkg" in Path(hal_project["test"]).read_text()


@when("se escanea el archivo test")
def _bdd_scan_test(hal_project: dict):
    from features.code_analysis.infrastructure.hallucination import scan_file

    hal_project["result"] = scan_file(
        Path(hal_project["test"]), Path(hal_project["root"])
    )


@then("se detecta al menos una alucinación")
def _bdd_hallucination(hal_project: dict):
    assert hal_project["result"]["hallucinations"] >= 1


@then("el módulo alucinado es \"nonexistent_pkg\"")
def _bdd_modulo_alucinado(hal_project: dict):
    for issue in hal_project["result"]["issues"]:
        if issue["status"] == "HALLUCINATION":
            assert issue["module"] == "nonexistent_pkg"


@then("el import del módulo válido \"mymod\" no se marca como alucinación")
def _bdd_no_marca_valido(hal_project: dict):
    for issue in hal_project["result"]["issues"]:
        assert not (issue["module"] == "mymod")


@given("un import a \"os\"")
def _bdd_import_os(hal_ctx: dict):
    hal_ctx["module"] = "os"


@when("se chequea con check_python_import")
def _bdd_check_import(hal_ctx: dict):
    from features.code_analysis.infrastructure.hallucination import (
        check_python_import,
    )

    status, _ = check_python_import(hal_ctx["module"], "", "", "")
    hal_ctx["status"] = status


@then("el status no es HALLUCINATION")
def _bdd_status_no_alucinado(hal_ctx: dict):
    assert hal_ctx["status"] != "HALLUCINATION"
