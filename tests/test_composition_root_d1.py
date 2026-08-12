"""tests/test_composition_root_d1.py — Composition root único (capa_rout.md D1).

Fija el comportamiento de la deduplicación D1:
  1. bootstrap_modules() es un singleton ESTABLE (todos los consumidores
     comparten el MISMO ModuleRegistry — antes se reconstruía por llamada).
  2. reset_module_registry() invalida el singleton (hot reload).
  3. tool_catalog._canonical_system_registry() consume bootstrap(separated=True)
     (sin registry efímero por llamada).
  4. get_module_service("workflow") expone submit/transition (los handlers ya
     NO instancian WorkflowRepository()/ScriptRunner() a mano).

Test-isolation: autouse teardown que resetea el singleton del ModuleRegistry
para que ningún test contamine al resto de la suite.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_module_registry():
    """Aisla el singleton del ModuleRegistry entre tests."""
    yield
    from features.engine.bootstrap import reset_module_registry

    reset_module_registry()


def test_bootstrap_modules_es_singleton_estable():
    """Todos los consumidores comparten el MISMO ModuleRegistry."""
    from features.engine.bootstrap import bootstrap_modules

    m1 = bootstrap_modules()
    m2 = bootstrap_modules()
    assert m1 is m2
    assert len(m1.list_services()) >= 5  # cognitive_searcher/graph/path/mcp/prompt/workflow


def test_reset_module_registry_invalida_el_singleton():
    """reset_module_registry() fuerza rebuild (patrón hot reload)."""
    from features.engine.bootstrap import bootstrap_modules, reset_module_registry

    m1 = bootstrap_modules()
    reset_module_registry()
    m2 = bootstrap_modules()
    assert m2 is not m1
    assert m2 is bootstrap_modules()


def test_catalog_consume_bootstrap_separated():
    """El registry canónico del catálogo = system_registry de bootstrap(separated=True)."""
    from features.engine.application.tool_catalog import _canonical_system_registry
    from features.engine.bootstrap import bootstrap

    _, system_registry, _ = bootstrap(separated=True)
    canonical = _canonical_system_registry()
    assert canonical.list_tasks() == system_registry.list_tasks()
    assert canonical.list_tasks(), "el catálogo no debe quedar vacío"
    # El catálogo debe incluir las tasks públicas esperadas.
    assert "kg_schema" in canonical.list_tasks()
    assert "kg_extract" in canonical.list_tasks()


def test_workflow_service_expone_las_cuatro_operaciones():
    """get_module_service('workflow') expone submit/transition/status/prepare.

    Los handlers ya NO importan use cases ni instancian repos a mano: las 4
    operaciones de workflow del CLI se resuelven por el MISMO composition root
    (capa_rout.md D1). status/prepare delegan en los orquestadores
    cross-feature de cli_interface.application.project_use_cases.
    """
    from features.engine.bootstrap import get_module_service

    workflow = get_module_service("workflow")
    assert callable(workflow.submit)
    assert callable(workflow.transition)
    assert callable(workflow.status)
    assert callable(workflow.prepare)
