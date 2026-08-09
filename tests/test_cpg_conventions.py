"""
Tests BDD (pytest-bdd) — Chequeo de convenciones del CKG contra el código.

Escenarios: tests/features/cpg_conventions.feature

El contrato valida que las convenciones registradas en el CKG (type=convention,
rule_yaml en metadata) se ejecutan contra el repo real del proyecto walker.
"""

from __future__ import annotations

import os

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/cpg_conventions.feature")


# ── Antecedentes ─────────────────────────────────────────────────


@pytest.fixture
def conv_ctx() -> dict:
    """Estado compartido entre steps."""
    return {}


@pytest.fixture
def walker_chain_dir() -> str:
    """Chain dir del proyecto real walker (registrado globalmente)."""
    return os.path.join(
        os.getcwd(), ".cognitive", "projects", "walker", "data", "spring"
    )


@given("un proyecto registrado con repositorio resoluble y CKG con convenciones")
def _bdd_raw_convenciones(conv_ctx: dict):
    # Antecedentes (Background): el estado real se fija en cada escenario.
    pass


@given("un chain_dir del proyecto walker registrado")
def _bdd_chain_dir(conv_ctx: dict, walker_chain_dir: str):
    conv_ctx["chain_dir"] = walker_chain_dir


@given("un chain_dir inexistente")
def _bdd_chain_inexistente(conv_ctx: dict):
    conv_ctx["chain_dir"] = "/ruta/inexistente"


@when("se ejecuta el chequeo de convenciones")
def _bdd_ejecuta_convenciones(conv_ctx: dict):
    from features.code_analysis.application import conventions

    conv_ctx["report"], conv_ctx["errors"] = conventions.execute(
        conv_ctx["chain_dir"]
    )


@then("el reporte indica al menos una regla comprobada")
def _bdd_reglas_comprobadas(conv_ctx: dict):
    assert conv_ctx["report"]["rules_checked"] >= 1, conv_ctx["report"]


@then("cada violación reportada tiene estructura de convención (rule, severity, file, line, text)")
def _bdd_estructura_violaciones(conv_ctx: dict):
    for v in conv_ctx["report"]["violations"]:
        assert "rule" in v and v["rule"]
        assert "severity" in v
        assert "file" in v and v["file"]
        assert isinstance(v["line"], int) and v["line"] > 0
        assert "text" in v and v["text"]


@then("las violaciones se agrupan por rule_id en el reporte")
def _bdd_by_rule(conv_ctx: dict):
    assert isinstance(conv_ctx["report"]["by_rule"], dict)


@then("no se reportan errores de ejecución")
def _bdd_sin_errores(conv_ctx: dict):
    assert not conv_ctx["errors"], conv_ctx["errors"]


@then("no hay violaciones reportadas")
def _bdd_sin_violaciones(conv_ctx: dict):
    assert conv_ctx["report"]["violations"] == []


@then("rules_checked es cero")
def _bdd_rules_cero(conv_ctx: dict):
    assert conv_ctx["report"]["rules_checked"] == 0
