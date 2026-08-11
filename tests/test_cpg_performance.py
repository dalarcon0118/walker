"""
Tests BDD (pytest-bdd) — Rendimiento build vs update incremental.

Escenarios: bdd/features/cpg_performance.feature

Contrato de eficiencia: index_task_files sobre un solo archivo debe tardar
menos que el build completo sobre N archivos.
"""

from __future__ import annotations

import os
import time

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/cpg_performance.feature")


@pytest.fixture
def perf_ctx() -> dict:
    return {}


@given("un builder del CPG con 100 módulos generados")
def _bdd_cien_modulos(tmp_path, perf_ctx: dict):
    src = os.path.join(str(tmp_path), "src")
    os.makedirs(src)
    for i in range(100):
        with open(os.path.join(src, f"mod_{i:03d}.py"), "w") as f:
            f.write(f"def fn_{i}():\n    return {i}\n\nclass C_{i}:\n    pass\n")

    from features.code_analysis.application.build_service import BuildGraphUseCase

    perf_ctx["builder"] = BuildGraphUseCase(
        project_id="perf",
        data_dir=str(tmp_path) + "/data",
        code_root=str(tmp_path),
    )
    perf_ctx["src"] = src


@when("se hace un build completo y luego un update incremental")
def _bdd_build_vs_update(perf_ctx: dict):
    t0 = time.time()
    result = perf_ctx["builder"].build([perf_ctx["src"]])
    perf_ctx["t_build"] = time.time() - t0
    assert result.get("stats", {}).get("total_nodes", 0) > 0

    changed = os.path.join(perf_ctx["src"], "mod_050.py")
    with open(changed, "a") as f:
        f.write("\ndef fn_050_new():\n    return 'changed'\n")

    t1 = time.time()
    result2 = perf_ctx["builder"].index_task_files([changed])
    perf_ctx["t_update"] = time.time() - t1
    perf_ctx["update"] = result2


@then("el update indexa el archivo cambiado")
def _bdd_update_ok(perf_ctx: dict):
    assert perf_ctx["update"].get("ok") is True, perf_ctx["update"]


@then("el update tarda menos que el build completo")
def _bdd_update_mas_rapido(perf_ctx: dict):
    assert perf_ctx["t_update"] < perf_ctx["t_build"], (
        f"Incremental ({perf_ctx['t_update']:.2f}s) debe ser más rápido que "
        f"full build ({perf_ctx['t_build']:.2f}s)"
    )