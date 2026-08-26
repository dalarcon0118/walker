"""
Tests BDD (pytest-bdd) — Caches incrementales de infraestructura del CPG.

Escenarios: bdd/features/cpg_infra_caches.feature

Cubre calls_cache (CallsCache) y scan_dfg (BuildGraphUseCase con cache +
async). Contrato: cache hits en la segunda corrida, invalidación al
modificar, persistencia entre instancias.

Nota: metrics_cache fue eliminado junto a qlty (ADR-WALKER-054).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/cpg_infra_caches.feature")


@pytest.fixture
def cache_ctx() -> dict:
    return {}


# ── Escenario 1: calls_cache ─────────────────────────────────────


@given("un módulo Python con funciones")
def _bdd_calls_mod(tmp_path: Path, cache_ctx: dict):
    fp = os.path.join(str(tmp_path), "mod.py")
    with open(fp, "w") as f:
        f.write("def foo():\n    return bar()\n\ndef bar():\n    return 42\n")
    cache_ctx["fp"] = fp
    cache_ctx["defs"] = [
        {"name": "foo", "line": 1, "end_line": 2},
        {"name": "bar", "line": 4, "end_line": 5},
    ]
    cache_ctx["cache_path"] = os.path.join(str(tmp_path), "data", "cfg_calls_cache.json")


@when("se detectan calls por primera vez y luego de nuevo")
def _bdd_calls_doble(cache_ctx: dict):
    from features.code_analysis.infrastructure.ast_grep_calls import (
        CallsCache,
        detect_calls_cached,
    )

    cache = CallsCache(cache_ctx["cache_path"])
    calls1, from_cache1 = detect_calls_cached(cache_ctx["fp"], cache_ctx["defs"], cache)
    cache_ctx["calls1"], cache_ctx["from_cache1"] = calls1, from_cache1
    calls2, from_cache2 = detect_calls_cached(cache_ctx["fp"], cache_ctx["defs"], cache)
    cache_ctx["calls2"], cache_ctx["from_cache2"] = calls2, from_cache2
    cache_ctx["cache"] = cache


@then("la segunda detección proviene del caché")
def _bdd_calls_cache_hit(cache_ctx: dict):
    assert not cache_ctx["from_cache1"]
    assert cache_ctx["from_cache2"]
    assert cache_ctx["calls2"] == cache_ctx["calls1"]


@then("el caché persiste entre instancias")
def _bdd_calls_persiste(cache_ctx: dict):
    from features.code_analysis.infrastructure.ast_grep_calls import (
        CallsCache,
        detect_calls_cached,
    )

    cache_ctx["cache"].save()
    c2 = CallsCache(cache_ctx["cache_path"])
    calls, from_cache = detect_calls_cached(cache_ctx["fp"], cache_ctx["defs"], c2)
    assert from_cache
    assert len(calls) > 0


# ── Escenario 2: scan_dfg cache ──────────────────────────────────


@given("un builder del CPG con un módulo que contiene un secreto")
def _bdd_builder_secreto(tmp_path: Path, cache_ctx: dict):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text("SECRET = 'sk-abc123'\ndef run():\n    print('x')\n")
    from features.code_analysis.application.build_service import BuildGraphUseCase

    cache_ctx["builder"] = BuildGraphUseCase(
        project_id="bdd-cache",
        data_dir=str(tmp_path / "data"),
        code_root=str(tmp_path),
    )
    cache_ctx["src"] = str(src)


@when("se escanea el dfg dos veces")
def _bdd_dfg_dos(cache_ctx: dict):
    r1 = cache_ctx["builder"].scan_dfg([cache_ctx["src"]], verbose=False)
    r2 = cache_ctx["builder"].scan_dfg([cache_ctx["src"]], verbose=False)
    cache_ctx["r1"], cache_ctx["r2"] = r1, r2


@then("la primera corrida no tiene cache_hits")
def _bdd_dfg_sin_hits(cache_ctx: dict):
    assert cache_ctx["r1"].get("_stats", {}).get("cache_hits") == 0


@then("la segunda corrida tiene cache_hits")
def _bdd_dfg_con_hits(cache_ctx: dict):
    assert cache_ctx["r2"].get("_stats", {}).get("cache_hits") >= 1


@then("los taint_paths coinciden entre corridas")
def _bdd_dfg_coinciden(cache_ctx: dict):
    assert len(cache_ctx["r2"].get("taint_paths", [])) == len(
        cache_ctx["r1"].get("taint_paths", [])
    )


# ── Escenario 3: scan_dfg async ──────────────────────────────────


@given("un builder del CPG con un módulo bajo src")
def _bdd_builder_async(tmp_path: Path, cache_ctx: dict):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text("SECRET = 'sk-abc123'\ndef run():\n    print('x')\n")
    from features.code_analysis.application.build_service import BuildGraphUseCase

    cache_ctx["builder"] = BuildGraphUseCase(
        project_id="bdd-async",
        data_dir=str(tmp_path / "data"),
        code_root=str(tmp_path),
    )
    cache_ctx["src"] = str(src)


@when("se lanza el scan dfg en background")
def _bdd_dfg_launch(cache_ctx: dict):
    cache_ctx["launch"] = cache_ctx["builder"].scan_dfg_async([cache_ctx["src"]])


@then("el estado inicial es running")
def _bdd_dfg_running(cache_ctx: dict):
    assert cache_ctx["launch"].get("status") == "running"
    assert os.path.exists(cache_ctx["launch"].get("status_path", ""))


@then("el estado final es ok")
def _bdd_dfg_ok(cache_ctx: dict):
    st = {}
    for _ in range(50):
        st = cache_ctx["builder"].dfg_scan_status()
        if st.get("status") in ("ok", "failed"):
            break
        time.sleep(0.1)
    assert st.get("status") == "ok"
    assert st.get("summary")
