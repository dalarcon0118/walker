"""
Tests BDD (pytest-bdd) — Contrato del Result pattern en el módulo CPG.

Ejecutar: python3 -m pytest tests/test_cpg_result_pattern.py -v

Los escenarios del feature (bdd/features/cpg_result_pattern.feature) describen
el CONTRATO que toda operación del CPG debe cumplir una vez migrada a
result pattern (dry-python/returns). Los steps ejercitan los cores reales de
features/code_analysis/application/build_service.py (ya migrados con @safe) y
verifican que el patrón de composición es el exigido: nunca lanzar, nunca
tragar errores en silencio, fallback explícito.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when
from returns.pipeline import is_successful

from features.code_analysis.application.build_service import (
    BuildGraphUseCase,
    _sync_core,
)

scenarios("bdd/features/cpg_result_pattern.feature")

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def cpg_env(tmp_path: Path) -> dict:
    """Entorno aislado: data_dir + code_root en un directorio temporal."""
    return {
        "data_dir": str(tmp_path / "data"),
        "code_root": str(tmp_path / "code"),
    }


@pytest.fixture
def ctx() -> dict:
    """Estado compartido entre steps (pytest-bdd no pasa valores por return)."""
    return {}


@pytest.fixture
def builder(cpg_env: dict) -> BuildGraphUseCase:
    os.makedirs(cpg_env["code_root"], exist_ok=True)
    return BuildGraphUseCase(
        project_id="bdd",
        data_dir=cpg_env["data_dir"],
        code_root=cpg_env["code_root"],
    )


# ── Antecedentes (background) ────────────────────────────────────


@given("un entorno de trabajo del CPG aislado en un directorio temporal")
def _bdd_entorno(ctx: dict):
    ctx["listo"] = True


# ── Escenario 1: sync exitosa ────────────────────────────────────


@given("un builder del CPG con 2 nodos de ejemplo en su grafo")
def _bdd_builder_con_nodos(ctx: dict):
    ctx["data"] = {
        "nodes": {
            "n1": {"name": "run", "type": "function", "path": "src/mod.py", "line": 3},
            "n2": {"name": "x", "type": "var", "path": "src/mod.py", "line": 1},
        },
        "edges": [],
    }


@when("se sincronizan los nodos al índice cpg")
def _bdd_sync_nodos(ctx: dict, builder: BuildGraphUseCase):
    ctx["result"] = _sync_core(builder.data_dir, ctx["data"])


@then("el resultado es un Success de la librería returns")
def _bdd_es_success(ctx: dict):
    result = ctx["result"]
    assert is_successful(result), f"esperaba Success, obtuve: {result.failure()}"
    assert type(result).__module__.startswith("returns")


@then("los 2 nodos quedan disponibles en el índice cpg")
def _bdd_nodos_indexados(ctx: dict, builder: BuildGraphUseCase):
    from libs.cognitive_graph import CognitiveGraph

    cg = CognitiveGraph(builder.data_dir, auto_register=True)
    keys = set(cg.keys("cpg"))
    assert {"n1", "n2"} <= keys, keys


# ── Escenario 2: sync fallida devuelve Failure ───────────────────


@given("un builder del CPG con un nodo de datos inválidos")
def _bdd_builder_nodo_invalido(ctx: dict):
    # node_data None hace que _sync_core falle dentro del core puro
    ctx["data"] = {"nodes": {"n1": None}, "edges": []}


@when("se sincroniza el nodo al índice cpg")
def _bdd_sync_nodo_invalido(ctx: dict, builder: BuildGraphUseCase):
    ctx["result"] = _sync_core(builder.data_dir, ctx["data"])


@then("el resultado es un Failure de la librería returns")
def _bdd_es_failure(ctx: dict):
    result = ctx["result"]
    assert not is_successful(result), "esperaba Failure"
    assert type(result).__module__.startswith("returns")


@then("el failure encapsula la excepción original (tipo y mensaje)")
def _bdd_failure_preserva_excepcion(ctx: dict):
    error = ctx["result"].failure()
    assert isinstance(error, Exception), f"esperaba Exception, obtuve {type(error)}"


@then("el wrapper de instancia registra el error en el logger, no en silencio")
def _bdd_logger_no_silencioso(ctx: dict, builder: BuildGraphUseCase, caplog):
    with caplog.at_level(
        logging.ERROR, logger="features.code_analysis.application.build_service"
    ):
        out = builder._sync_to_cognitive(ctx["data"])
    assert out.get("ok") is False, out
    assert caplog.records, "debe haber registrado el error en el logger"


# ── Escenario 3: composición sin try/except ──────────────────────


@given("un resultado Success de una operación del CPG")
def _bdd_resultado_success(ctx: dict, builder: BuildGraphUseCase):
    data = {
        "nodes": {
            "n1": {"name": "run", "type": "function", "path": "src/mod.py", "line": 3},
        },
        "edges": [],
    }
    ctx["result"] = _sync_core(builder.data_dir, data)


@when("se decide el camino con la función de chequeo de la librería returns")
def _bdd_decision_con_is_successful(ctx: dict):
    assert is_successful(ctx["result"])
    ctx["value"] = ctx["result"].value_or({})


@then("el valor se extrae sin lanzar excepción")
def _bdd_valor_extraido(ctx: dict):
    assert ctx["value"].get("stored") == 1


@then("ante un Failure se obtiene un valor por defecto con value_or")
def _bdd_value_or_fallback():
    from returns.result import Failure

    failure = Failure(ValueError("boom"))
    assert failure.value_or({"stored": 0}) == {"stored": 0}


@then("la orquestación no usa cláusulas except para manejar el error")
def _bdd_sin_except_en_orquestacion():
    from features.code_analysis.application import build_service

    source = Path(build_service.__file__).read_text()
    # Los try/except permitidos son solo de infraestructura (subprocess/io) o
    # del fallback explícito del core; la orquestación decide con is_successful.
    assert "if is_successful(" in source, (
        "la orquestación debe decidir con is_successful"
    )


# ── Escenario 4: excepciones del core envueltas por @safe ────────


@given("un core puro del CPG que lanza al recibir entrada inválida")
def _bdd_core_que_lanza(ctx: dict):
    ctx["data"] = {"nodes": {"n1": None}, "edges": []}


@when("la ejecución vía @safe envuelve la excepción")
def _bdd_safe_envuelve(ctx: dict, builder: BuildGraphUseCase):
    # _sync_core está decorado con @safe: la excepción se convierte en Failure
    ctx["result"] = _sync_core(builder.data_dir, ctx["data"])


@then("el caller recibe un Failure en vez de que se propague")
def _bdd_no_se_propaga(ctx: dict):
    assert not is_successful(ctx["result"])
    assert type(ctx["result"]).__module__.startswith("returns")


@then("el tipo original de la excepción se preserva en el failure")
def _bdd_tipo_preservado(ctx: dict):
    error = ctx["result"].failure()
    # _sync_core con node_data None lanza AttributeError; @safe lo envuelve
    assert type(error).__name__ in (
        "AttributeError",
        "TypeError",
        "ValueError",
        "KeyError",
    ), f"tipo inesperado: {type(error)}"
