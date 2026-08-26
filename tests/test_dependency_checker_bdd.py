"""Tests BDD (pytest-bdd) — Dependency Checker.

Escenarios: bdd/features/dependency_checker.feature
"""
from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/dependency_checker.feature")


@pytest.fixture
def dep_ctx() -> dict:
    return {}


@given("que limpio el cache", target_fixture="dep_ctx")
def _clean_cache(dep_ctx: dict):
    from features.dependency_checker import reset_cache
    reset_cache()
    return dep_ctx


@given("que el cache tiene python3 disponible", target_fixture="dep_ctx")
def _cache_python3(dep_ctx: dict):
    from features.dependency_checker import _cache
    _cache["python3"] = True
    return dep_ctx


@given("que el cache tiene missing-tool-xyz no disponible", target_fixture="dep_ctx")
def _cache_missing(dep_ctx: dict):
    from features.dependency_checker import _cache
    _cache["missing-tool-xyz"] = False
    return dep_ctx


@given("que el cache tiene ast-grep disponible", target_fixture="dep_ctx")
def _cache_ast_grep(dep_ctx: dict):
    from features.dependency_checker import _cache
    _cache["ast-grep"] = True
    return dep_ctx


@when("consulto is_available python3", target_fixture="dep_ctx")
def _is_available(dep_ctx: dict):
    from features.dependency_checker import is_available
    dep_ctx["available"] = is_available("python3")
    return dep_ctx


@when("ejecuto ensure_tools con python3", target_fixture="dep_ctx")
def _ensure_python3(dep_ctx: dict):
    from features.dependency_checker import ensure_tools
    dep_ctx["missing"] = ensure_tools(["python3"])
    return dep_ctx


@when("ejecuto ensure_tools con missing-tool-xyz", target_fixture="dep_ctx")
def _ensure_missing(dep_ctx: dict):
    from features.dependency_checker import ensure_tools
    dep_ctx["missing"] = ensure_tools(["missing-tool-xyz"])
    return dep_ctx


@when("ejecuto ensure_ast_grep", target_fixture="dep_ctx")
def _ensure_ast_grep(dep_ctx: dict):
    from features.dependency_checker import ensure_ast_grep
    dep_ctx["result"] = ensure_ast_grep()
    return dep_ctx


@when("ejecuto reset_cache", target_fixture="dep_ctx")
def _reset_cache(dep_ctx: dict):
    from features.dependency_checker import reset_cache
    reset_cache()
    return dep_ctx


@then("el resultado es True")
def _assert_true(dep_ctx: dict):
    assert dep_ctx.get("available") is True or dep_ctx.get("result") is True


@then("el resultado es vacío")
def _assert_empty(dep_ctx: dict):
    assert dep_ctx.get("missing") == []


@then("el resultado contiene missing-tool-xyz")
def _assert_contains_missing(dep_ctx: dict):
    missing = dep_ctx.get("missing", [])
    assert "missing-tool-xyz" in missing


@then("el cache está vacío")
def _assert_cache_empty(dep_ctx: dict):
    from features.dependency_checker import _cache
    assert len(_cache) == 0
