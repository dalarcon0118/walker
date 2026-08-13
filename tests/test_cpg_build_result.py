"""
Tests BDD (pytest-bdd) — Result pattern en el build del CPG.

Escenarios: bdd/features/cpg_build_result.feature

Cubre _sync_core (@safe) de build_service.py y el wrapper _sync_to_cognitive
que loggea el error (convention-001: nunca except silencioso).
"""

from __future__ import annotations

import logging
import os

import pytest
from pytest_bdd import given, scenarios, then, when
from returns.pipeline import is_successful

scenarios("bdd/features/cpg_build_result.feature")


@pytest.fixture
def br_ctx() -> dict:
    return {}


@pytest.fixture
def br_builder(tmp_path, br_ctx: dict):
    from features.code_analysis.application.build_service import BuildGraphUseCase

    os.makedirs(str(tmp_path / "code"), exist_ok=True)
    builder = BuildGraphUseCase(
        project_id="bdd-build",
        data_dir=str(tmp_path / "data"),
        code_root=str(tmp_path / "code"),
    )
    br_ctx["builder"] = builder
    return builder


@given("un builder del CPG con 2 nodos válidos en su grafo")
def _bdd_nodos_validos(br_ctx: dict):
    br_ctx["data"] = {
        "nodes": {
            "n1": {"name": "run", "type": "function", "path": "src/mod.py", "line": 3},
            "n2": {"name": "x", "type": "var", "path": "src/mod.py", "line": 1},
        },
        "edges": [],
    }


@given("un builder del CPG con un nodo con datos inválidos")
def _bdd_nodo_invalido(br_ctx: dict):
    br_ctx["data"] = {"nodes": {"n1": None}, "edges": []}


@when("se sincronizan los nodos al índice cpg")
def _bdd_sync(br_ctx: dict, br_builder):
    from features.code_analysis.application.build_service import _sync_core

    br_ctx["result"] = _sync_core(br_ctx["builder"].data_dir, br_ctx["data"])


@then("el resultado es un Success de returns")
def _bdd_success(br_ctx: dict):
    result = br_ctx["result"]
    assert is_successful(result), f"esperaba Success, obtuve: {result.failure()}"
    assert type(result).__module__.startswith("returns")


@then("quedan 2 nodos disponibles en el índice cpg")
def _bdd_nodos_indexados(br_ctx: dict):
    from shared.core.cognitive_graph import CognitiveGraph

    cg = CognitiveGraph(br_ctx["builder"].data_dir, auto_register=True)
    keys = set(cg.keys("cpg"))
    assert {"n1", "n2"} <= keys, keys


@when("se sincroniza el nodo al índice cpg")
def _bdd_sync_invalido(br_ctx: dict, br_builder):
    from features.code_analysis.application.build_service import _sync_core

    br_ctx["result"] = _sync_core(br_ctx["builder"].data_dir, br_ctx["data"])


@then("el resultado es un Failure de returns")
def _bdd_failure(br_ctx: dict):
    result = br_ctx["result"]
    assert not is_successful(result), "esperaba Failure"
    assert type(result).__module__.startswith("returns")


@then("el wrapper de instancia loggea el error")
def _bdd_log_not_silent(br_ctx: dict, caplog):
    from returns.pipeline import is_successful as is_succ

    assert not is_succ(br_ctx["result"])
    with caplog.at_level(
        logging.ERROR,
        logger="features.code_analysis.application.build_service",
    ):
        out = br_ctx["builder"]._sync_to_cognitive(br_ctx["data"])
    assert out.get("ok") is False, out
    assert caplog.records, "debe haber registrado el error"