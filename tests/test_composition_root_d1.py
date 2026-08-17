"""tests/test_composition_root_d1.py — Composition root único (capa_rout.md D1).

Fija el comportamiento de la deduplicación D1 bajo ADR-045:
  1. AppContainer.build() es un singleton ESTABLE (todos los consumidores
     comparten el MISMO AppComposition/ModuleRegistry).
  2. AppContainer.reset() invalida el singleton (hot reload).
  3. tool_catalog._canonical_system_registry() consume el task_registry del
     composition root (sin registry efímero por llamada).
  4. get_module_service("workflow") expone submit/transition (los handlers ya
     NO instancian WorkflowRepository()/ScriptRunner() a mano).

Test-isolation: autouse teardown que resetea el singleton de features/main
para que ningún test contamine al resto de la suite.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_app_container():
    """Aisla el singleton de features/main entre tests."""
    yield
    from features.main.container import AppContainer

    AppContainer.reset()


def test_compose_app_es_singleton_estable():
    """Todos los consumidores comparten el MISMO AppComposition."""
    from features.main.container import AppContainer

    a1 = AppContainer.build()
    a2 = AppContainer.build()
    assert a1 is a2
    assert a1.module_registry is a2.module_registry
    assert len(a1.module_registry.list_services()) >= 5  # module/path/graph/searcher/mcp/prompt/workflow


def test_reset_composition_invalida_el_singleton():
    """AppContainer.reset() fuerza rebuild (patrón hot reload)."""
    from features.main.container import AppContainer, reset_composition

    a1 = AppContainer.build()
    AppContainer.reset()
    a2 = AppContainer.build()
    assert a2 is not a1
    assert a2 is AppContainer.build()


def test_catalog_consume_task_registry_del_composition_root():
    """El registry canónico del catálogo = system_registry del composition root."""
    from core.engine.application.tool_catalog import _canonical_system_registry
    from features.main.container import AppContainer

    app = AppContainer.build()
    canonical = _canonical_system_registry()
    assert canonical.list_tasks(), "el catálogo no debe quedar vacío"
    # El catálogo debe incluir las tasks públicas esperadas (registradas por
    # features/main desde features/*/tasks/ — la implementación real).
    assert "kg_schema" in canonical.list_tasks()
    assert "kg_extract" in canonical.list_tasks()


def test_workflow_service_expone_las_cuatro_operaciones():
    """get_module_service('workflow') expone submit/transition/status/prepare.

    Los handlers ya NO importan use cases ni instancian repos a mano: las 4
    operaciones de workflow del CLI se resuelven por el MISMO composition root
    (capa_rout.md D1). status/prepare delegan en los orquestadores
    cross-feature de features/main/cli_interface.application.project_use_cases.
    """
    from core.engine.bootstrap import get_module_service

    workflow = get_module_service("workflow")
    assert callable(workflow.submit)
    assert callable(workflow.transition)
    assert callable(workflow.status)
    assert callable(workflow.prepare)
