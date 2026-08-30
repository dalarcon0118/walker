"""Tests BDD (pytest-bdd) — Module registry DI.

Feature value: valida que el ModuleRegistry registre servicios por clave
lógica con resolución lazy singleton, y que los features reciban servicios
sin importar implementaciones concretas.

Escenarios: bdd/features/module_registry_di.feature
"""
from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/module_registry_di.feature")


@pytest.fixture
def module_registry_di_ctx() -> dict:
    return {}


# ── Given ─────────────────────────────────────────────────────────────────────


@given("un ModuleRegistry vacío")
def _empty_registry(module_registry_di_ctx: dict):
    from shared.libs.module_system.registry import InMemoryModuleRegistry

    module_registry_di_ctx["registry"] = InMemoryModuleRegistry()


@given("un ModuleRegistry con factory lazy para \"searcher\"")
def _registry_with_lazy(module_registry_di_ctx: dict):
    from shared.libs.module_system.registry import InMemoryModuleRegistry

    calls = {"n": 0}

    def _factory():
        calls["n"] += 1
        return {"built": calls["n"]}

    registry = InMemoryModuleRegistry()
    registry.register_service("searcher", _factory, lazy=True)
    module_registry_di_ctx["registry"] = registry
    module_registry_di_ctx["calls"] = calls


@given("el composition root bootstrap")
def _bootstrap_ready(module_registry_di_ctx: dict):
    from app.container import AppContainer

    app = AppContainer.build()
    module_registry_di_ctx["registry"] = app.module_registry


@given("un registry con \"cognitive_searcher\" lazy")
def _registry_with_cognitive_searcher(module_registry_di_ctx: dict):
    from shared.libs.module_system.registry import InMemoryModuleRegistry

    registry = InMemoryModuleRegistry()
    registry.register_service(
        "cognitive_searcher", lambda: {"reader": True}, lazy=True
    )
    module_registry_di_ctx["registry"] = registry


# ── When ──────────────────────────────────────────────────────────────────────


@when('registro el servicio "path_resolver"')
def _register_path_resolver(module_registry_di_ctx: dict):
    registry = module_registry_di_ctx["registry"]
    registry.register_service("path_resolver", object())


@when('resuelvo "searcher" dos veces')
def _resolve_searcher_twice(module_registry_di_ctx: dict):
    registry = module_registry_di_ctx["registry"]
    module_registry_di_ctx["first"] = registry.get_service("searcher")
    module_registry_di_ctx["second"] = registry.get_service("searcher")


@when('intento resolver "no_existe"')
def _resolve_missing(module_registry_di_ctx: dict):
    registry = module_registry_di_ctx["registry"]
    with pytest.raises(KeyError):
        registry.get_service("no_existe")
    module_registry_di_ctx["raised"] = True


@when("inicializo el registry con bootstrap_modules")
def _bootstrap_modules(module_registry_di_ctx: dict):
    from app.container import AppContainer

    app = AppContainer.build()
    module_registry_di_ctx["registry"] = app.module_registry


@when("construyo un adapter resolviendo por clave")
def _build_adapter(module_registry_di_ctx: dict):
    registry = module_registry_di_ctx["registry"]

    def build_feature_adapter(r):
        searcher = r.get_service("cognitive_searcher")
        return searcher["reader"]

    module_registry_di_ctx["adapter"] = build_feature_adapter(registry)


# ── Then ──────────────────────────────────────────────────────────────────────


@then('el servicio existe en el registry')
def _service_exists(module_registry_di_ctx: dict):
    assert module_registry_di_ctx["registry"].has_service("path_resolver")


@then('al resolverlo obtengo una instancia')
def _resolve_instance(module_registry_di_ctx: dict):
    assert module_registry_di_ctx["registry"].get_service("path_resolver") is not None


@then('aparece en list_services')
def _in_list_services(module_registry_di_ctx: dict):
    assert "path_resolver" in module_registry_di_ctx["registry"].list_services()


@then('obtengo la misma instancia')
def _same_instance(module_registry_di_ctx: dict):
    assert module_registry_di_ctx["first"] is module_registry_di_ctx["second"]


@then('la factory se construyó solo una vez')
def _built_once(module_registry_di_ctx: dict):
    assert module_registry_di_ctx["calls"]["n"] == 1


@then('se lanza KeyError')
def _keyerror_raised(module_registry_di_ctx: dict):
    assert module_registry_di_ctx["raised"]


@then('"path_resolver" está registrado y es callable')
def _path_resolver_callable(module_registry_di_ctx: dict):
    registry = module_registry_di_ctx["registry"]
    assert registry.has_service("path_resolver")
    assert callable(getattr(registry.get_service("path_resolver"), "get_walker_root", None))


@then('"cognitive_searcher" está registrado como factory callable')
def _cognitive_searcher_callable(module_registry_di_ctx: dict):
    registry = module_registry_di_ctx["registry"]
    assert registry.has_service("cognitive_searcher")
    factory = registry.get_service("cognitive_searcher")
    assert callable(factory)


@then('"mcp_server" está registrado como lazy singleton')
def _mcp_server_singleton(module_registry_di_ctx: dict):
    registry = module_registry_di_ctx["registry"]
    server = registry.get_service("mcp_server")
    assert server is registry.get_service("mcp_server")


@then('obtengo el servicio sin importar implementaciones')
def _adapter_ok(module_registry_di_ctx: dict):
    assert module_registry_di_ctx["adapter"] is True
