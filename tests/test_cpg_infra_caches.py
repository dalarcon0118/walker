"""
Tests BDD (pytest-bdd) — Caches incrementales de infraestructura del CPG.

Escenarios: tests/features/cpg_infra_caches.feature

Cubre metrics_cache (IncrementalMetricsCache), calls_cache (CallsCache) y
scan_dfg (BuildGraphUseCase con cache + async). Contrato: cache hits en la
segunda corrida, invalidación al modificar, persistencia entre instancias.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/cpg_infra_caches.feature")


@pytest.fixture
def cache_ctx() -> dict:
    return {}


# ── Escenario 1: metrics_cache ───────────────────────────────────


@given("un módulo Python y un caché de métricas")
def _bdd_metrics_cache(tmp_path: Path, cache_ctx: dict):
    f = tmp_path / "mod.py"
    f.write_text("def a():\n    return 1\n")
    cache_ctx["file"] = str(f)
    cache_ctx["tmp"] = str(tmp_path)
    from features.code_analysis.infrastructure.metrics_cache import (
        IncrementalMetricsCache,
    )

    cache_ctx["cache"] = IncrementalMetricsCache(
        str(tmp_path), data_dir=str(tmp_path / "data")
    )


@when("se consultan los archivos stale")
def _bdd_metrics_stale(cache_ctx: dict):
    fresh, stale = cache_ctx["cache"].get_stale_files([cache_ctx["tmp"]])
    cache_ctx["fresh"], cache_ctx["stale"] = fresh, stale


@then("el módulo es stale")
def _bdd_metrics_es_stale(cache_ctx: dict):
    assert cache_ctx["file"] in cache_ctx["stale"]
    assert cache_ctx["file"] not in cache_ctx["fresh"]


@then("al actualizarlo el caché lo marca fresco")
def _bdd_metrics_fresco(cache_ctx: dict):
    cache_ctx["cache"].update(
        cache_ctx["file"], metrics={"file": cache_ctx["file"], "cyclo": 0}
    )
    cache_ctx["cache"].save()
    cache2 = cache_ctx["cache"].__class__(
        cache_ctx["tmp"], data_dir=str(Path(cache_ctx["tmp"]) / "data")
    )
    fresh2, stale2 = cache2.get_stale_files([cache_ctx["tmp"]])
    assert cache_ctx["file"] in fresh2
    assert cache_ctx["file"] not in stale2


@then("al modificar el archivo se vuelve stale")
def _bdd_metrics_reinvalida(cache_ctx: dict):
    Path(cache_ctx["file"]).write_text(
        "def a():\n    return 1\n\ndef b():\n    return 2\n"
    )
    fresh3, stale3 = cache_ctx["cache"].get_stale_files([cache_ctx["tmp"]])
    assert cache_ctx["file"] not in fresh3
    assert cache_ctx["file"] in stale3


# ── Escenario 2: calls_cache ─────────────────────────────────────


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


# ── Escenario 3: scan_dfg cache ──────────────────────────────────


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


# ── Escenario 4: scan_dfg async ──────────────────────────────────


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


# ── Escenario 5: expand_paths ────────────────────────────────────


@given("un directorio con archivos .py, .js, .md y .pyc")
def _bdd_expand_dir(tmp_path: Path, cache_ctx: dict):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.js").write_text("const x = 1\n")
    (tmp_path / "README.md").write_text("# doc\n")
    (tmp_path / "skip_me.pyc").write_bytes(b"\x00")
    cache_ctx["tmp"] = str(tmp_path)
    from features.code_analysis.infrastructure.metrics_cache import (
        IncrementalMetricsCache,
    )

    cache_ctx["cache"] = IncrementalMetricsCache(
        str(tmp_path), data_dir=str(tmp_path / "data")
    )


@when("se expanden los paths")
def _bdd_expand(cache_ctx: dict):
    cache_ctx["files"] = cache_ctx["cache"]._expand_paths([cache_ctx["tmp"]])


@then("incluye los .py y .js")
def _bdd_expand_incluye(cache_ctx: dict):
    names = {os.path.basename(f) for f in cache_ctx["files"]}
    assert "a.py" in names and "b.js" in names


@then("excluye README.md y el .pyc")
def _bdd_expand_excluye(cache_ctx: dict):
    names = {os.path.basename(f) for f in cache_ctx["files"]}
    assert "README.md" not in names
    assert "skip_me.pyc" not in names
