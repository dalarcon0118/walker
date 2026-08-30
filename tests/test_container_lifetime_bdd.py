"""tests/test_container_lifetime_bdd.py — Ciclo de vida del AppContainer (ADR-046).

Fija el comportamiento del container con ciclo de vida explícito:
  1. build() es singleton ESTABLE (todos comparten el MISMO container).
  2. resolve() solo lee instancias de build(); KeyError si no existe.
  3. child() es efímero (ctx + chain_dir + task_id), no construye nada.
  4. Build diferido (force=True) conserva el container actual en fallo
     (ADR-046 failure_scenario #2: nunca degrade).
  5. reset_composition() invalida el container completo (failure_scenario #10).

Test-isolation: teardown que resetea el container para no contaminar la suite.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_container():
    yield
    from app.container import AppContainer

    AppContainer.reset()


def test_build_es_singleton_estable():
    """Todos los consumidores comparten el MISMO AppContainer/module_registry."""
    from app.container import AppContainer

    c1 = AppContainer.build()
    c2 = AppContainer.build()
    assert c1 is c2
    assert c1.module_registry is c2.module_registry
    assert c1.workflow is c2.workflow
    assert c1.task_registry is c2.task_registry


def test_resolve_devuelve_instancias_registradas():
    """resolve() lee las instancias ya construidas en build()."""
    from app.container import AppContainer

    container = AppContainer.build()
    assert container.resolve("module_registry") is container.module_registry
    assert container.resolve("task_registry") is container.task_registry
    assert container.resolve("side_effect_engine") is container.side_effect_engine
    workflow = container.resolve("workflow")
    assert callable(workflow.submit)
    assert callable(workflow.transition)
    assert callable(workflow.status)
    assert callable(workflow.prepare)


def test_resolve_keyerror_si_no_existe():
    """resolve() con clave desconocida lanza KeyError (contrato literal)."""
    from app.container import AppContainer

    container = AppContainer.build()
    with pytest.raises(KeyError):
        container.resolve("servicio_que_no_existe")


def test_child_es_efimero():
    """child() es el único objeto efímero por request; no construye nada."""
    from app.container import AppContainer

    container = AppContainer.build()
    scope = container.child(chain_dir="/tmp/chain", task_id="T1")
    assert scope.chain_dir == "/tmp/chain"
    assert scope.task_id == "T1"
    assert scope.container is container
    assert scope.ctx is not None
    assert scope.ctx.task_id == "T1"
    # No muta los singletons del container.
    assert container.workflow is AppContainer.build().workflow


def test_build_diferido_conserva_container_en_fallo(monkeypatch):
    """Si el build diferido falla, se conserva el container actual (nunca degrade)."""
    from app.container import AppContainer

    c1 = AppContainer.build()

    def _boom(*args, **kwargs):
        raise RuntimeError("build roto a mitad (registro)")

    # ADR-047 Fase 3: el registro de tasks es declarativo (features/main/
    # task_execution.tasks), ya no hay _discover_and_register_tasks en container.
    monkeypatch.setattr(
        "tasks.actions.register_task_execution_tasks", _boom
    )
    with pytest.raises(RuntimeError):
        AppContainer.build(force=True)
    # El swap atómico NO ocurrió: el container actual sigue siendo c1.
    assert AppContainer.build() is c1


def test_build_diferido_reemplaza_container_en_exito(monkeypatch):
    """Si el build diferido es exitoso, el container nuevo reemplaza al viejo."""
    from app.container import AppContainer

    c1 = AppContainer.build()
    # Forzamos un build nuevo exitoso (mismo grafo, instancias nuevas).
    c2 = AppContainer.build(force=True)
    assert c2 is not c1
    assert AppContainer.build() is c2


def test_reset_composition_invalida_container():
    """reset_composition() fuerza rebuild (patrón hot reload) + resetea el app."""
    from app.container import AppContainer

    c1 = AppContainer.build()
    AppContainer.reset()
    c2 = AppContainer.build()
    assert c2 is not c1
    assert c2 is AppContainer.build()
