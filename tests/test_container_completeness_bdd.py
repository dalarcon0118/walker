"""tests/test_container_completeness_bdd.py — Completitud del AppContainer (ADR-046).

Patrón obligatorio del AGENTS.md: todo registry construido por bootstrap/build_*()
debe tener un test de completitud. ADR-046 Fase 2: el test pasa a verificar el
CONTAINER completo — servicios DI (workflow/task_registry/side_effect_engine/
module_registry/cognitive_*/path_resolver/mcp_server/prompt_manager) y providers
de contexto (business, ckg, cpg, delta, diff, project, reflection, state, task,
test_results, trace).

También fija el registro idempotente: re-registrar un provider ya existente NO
genera warning ni pisa el existente (failure_scenario #7 del ADR-046).
"""

from __future__ import annotations


EXPECTED_SERVICES = {
    "module_registry",
    "task_registry",
    "side_effect_engine",
    "workflow",
    "cognitive_searcher",
    "cognitive_graph",
    "path_resolver",
    "mcp_server",
    "prompt_manager",
}

EXPECTED_PROVIDERS = {
    "business",
    "ckg",
    "cpg",
    "delta",
    "diff",
    "project",
    "reflection",
    "state",
    "task",
    "task_intelligence",
    "test_results",
    "trace",
}


class TestContainerCompleteness:
    """AppContainer.build() debe producir el grafo completo (composition root D1)."""

    def test_container_services_completeness(self):
        from features.main.container import AppContainer

        container = AppContainer.build()
        available = set(container.module_registry.list_services())
        missing = EXPECTED_SERVICES - available
        assert not missing, f"AppContainer no registró servicios: {sorted(missing)}"
        # resolve() no debe lanzar KeyError para ningún servicio esperado.
        for name in EXPECTED_SERVICES:
            container.resolve(name)

    def test_container_providers_completeness(self):
        """El registry de contexto del container incluye TODOS los providers."""
        from features.main.container import AppContainer

        container = AppContainer.build()
        registry = container.workflow._context_registry
        assert registry is not None, "container sin context_registry"
        available = set(registry.available())
        missing = EXPECTED_PROVIDERS - available
        extra = available - EXPECTED_PROVIDERS
        assert not missing, f"Faltan providers: {sorted(missing)}"
        assert not extra, f"Providers inesperados: {sorted(extra)}"
        assert len(available) == len(EXPECTED_PROVIDERS)

    def test_registration_idempotent(self, caplog):
        """Registrar el mismo provider 2 veces no genera warning ni lo pisa.

        ADR-046 failure_scenario #7: los warnings 'Provider X already
        registered — overwriting' desaparecen; el registro verifica duplicados
        ANTES de registrar (aditivo, no over-write).
        """
        import logging

        from core.engine.context import ContextProviderRegistry
        from core.engine.context.providers import DiffProvider

        registry = ContextProviderRegistry()
        provider = DiffProvider()
        registry.register("diff", provider)
        assert "diff" in registry.available()
        # Re-registrar no debe lanzar y debe conservar el existente.
        with caplog.at_level(logging.WARNING, logger="core.engine.context.registry"):
            registry.register("diff", DiffProvider())
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING], (
            "re-registrar un provider no debe emitir warnings de overwrite"
        )
        assert registry.available().count("diff") == 1
        assert registry.get("diff") is provider

    def test_di_exposes_container_services(self):
        """El facade neutro de core/engine resuelve desde el container."""
        from core.engine.bootstrap import get_module_service
        from features.main.container import AppContainer

        AppContainer.build()
        assert callable(get_module_service("workflow").submit)
        assert callable(get_module_service("workflow").status)
        task_registry = get_module_service("task_registry")
        assert task_registry is AppContainer.build().task_registry
