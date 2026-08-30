"""app/module.py — Módulo HOST de composición (estilo NestJS, ADR-066).

Declara SOLO los INGREDIENTES del host como factories lazy que inyectan por
id+contrato desde el ModuleRegistry (DI NestJS): workflow_store, actions,
task_registry, side_effect_engine, tracer, context_registry, etc.

La INSTANCIA del engine NO vive aquí: la construye **`main`** (la función de
composición `register_all`, entry point de la app) con todos sus parámetros
explícitos desde estos providers (patrón servidor, ADR-WALKER-067 §2.4).

- `shared` se autoregistra (módulo "shared.engine" + adaptadores por defecto).
- Este módulo host OVERRIDEA `workflow_repo`/`state_repo` con los paths reales
  del proyecto (register_service reemplaza — último registro gana).
- El registro de tasks/side-effects (imperativo por naturaleza) vive en el
  hook `register` de ESTE módulo, contra los providers del registry.
"""
from __future__ import annotations

from typing import Any

from shared.libs.module_system.contracts import ModuleManifest


# ── Factories de providers (DI por id+contrato) ───────────────────────────


def _build_tracer(reg: Any) -> Any:
    from app.composition.registry import _build_tracer_core

    return _build_tracer_core()


def _build_context_registry(reg: Any) -> Any:
    from shared.core.engine.bootstrap import build_context_registry

    return build_context_registry()


def _build_workflow_repo(reg: Any) -> Any:
    from shared.core.engine.infrastructure.repositories import WorkflowRepository
    from shared.core.engine.infrastructure.config import get_workflow_manifest_path

    # ADR-WALKER-061: manifiesto ÚNICO desde config central.
    return WorkflowRepository(workflow_path=get_workflow_manifest_path())


def _build_state_repo(reg: Any) -> Any:
    from shared.core.engine.infrastructure.repositories import WorkflowStateRepository

    return WorkflowStateRepository()


def _build_task_registry(reg: Any) -> Any:
    from shared.core.engine.infrastructure.task_registry import InMemoryTaskRegistry

    return InMemoryTaskRegistry(tracer=reg.get_service("tracer"))


def _build_side_effect_engine(reg: Any) -> Any:
    from shared.core.engine.infrastructure.side_effects import SideEffectEngine

    return SideEffectEngine(tracer=reg.get_service("tracer"))


def _build_workflow_store(reg: Any) -> Any:
    from shared.core.engine.infrastructure.workflow_store import WorkflowStoreAdapter

    return WorkflowStoreAdapter(
        workflow_repo=reg.get_service("workflow_repo"),
        state_repo=reg.get_service("state_repo"),
    )


def _build_action_catalog(reg: Any) -> Any:
    from shared.core.engine.domain.workflow_action import (
        ActionCatalog,
        KIND_SIDE_EFFECT,
        KIND_SUB_STATE_ACTION,
    )

    tr = reg.get_service("task_registry")
    se = reg.get_service("side_effect_engine")
    catalog = ActionCatalog()
    for meta in tr.list_tasks_with_meta(scope=None):
        catalog.register(
            name=meta["name"],
            kind=KIND_SUB_STATE_ACTION,
            scope=meta.get("scope", "public"),
            handler=tr.get_task(meta["name"]),
            metadata=meta,
        )
    for name, handler in se._handlers.items():
        catalog.register(
            name=name,
            kind=KIND_SIDE_EFFECT,
            scope="internal",
            handler=handler,
            metadata={"source": "side_effect_engine"},
        )
    # Wiring de composición (el engine lo recibe por constructor vía factory).
    se._action_catalog = catalog
    return catalog


def _register_host(reg: Any) -> None:
    """Registra los providers del host (factories lazy, DI por id+contrato).

    SOLO los INGREDIENTES del engine (workflow_store, actions, task_registry,
    etc.). La INSTANCIA del engine NO vive aquí: la construye `main` (la
    función de composición `register_all`, entry point de la app) con todos
    sus parámetros explícitos desde estos providers (patrón servidor).
    """
    # workflow_repo/state_repo EAGER (override de los defaults host-agnósticos
    # que `shared` registró: último registro gana).
    reg.register_service("workflow_repo", _build_workflow_repo(reg))
    reg.register_service("state_repo", _build_state_repo(reg))
    reg.register_service("workflow_store", _build_workflow_store(reg))
    # El resto LAZY (se instancian al primer get_service).
    reg.register_service("tracer", lambda: _build_tracer(reg), lazy=True)
    reg.register_service("context_registry", lambda: _build_context_registry(reg), lazy=True)
    reg.register_service("task_registry", lambda: _build_task_registry(reg), lazy=True)
    reg.register_service("side_effect_engine", lambda: _build_side_effect_engine(reg), lazy=True)
    reg.register_service("action_catalog", lambda: _build_action_catalog(reg), lazy=True)

    # Registro de tasks/side-effects del workflow (ADR-047 Fase 3): se hace
    # aquí (composición) contra los providers task_registry/side_effect_engine.
    from tasks.actions import register_task_execution_tasks
    from tasks.side_effects import register_task_execution_side_effects
    from app.composition.registry import (
        _discover_and_register_side_effects,
        _discover_and_register_tasks,
    )

    tr = reg.get_service("task_registry")
    se = reg.get_service("side_effect_engine")
    register_task_execution_tasks(tr)
    register_task_execution_side_effects(se)
    _discover_and_register_tasks(tr)
    _discover_and_register_side_effects(se)


MODULE = ModuleManifest(
    name="app",
    imports=[
        "shared",
        "tasks",
        "features.main.prompts",
        "features.project",
        "features.delta_tracking",
        "features.code_analysis",
        "features.cognitive_knowledge",
        "features.event_tracing",
    ],
    register=_register_host,
)
# Auto-registro por import (ADR-WALKER-068): el composition root importa este
# modulo; al importarlo se registra su manifest. Sin lista central de paths.
from shared.libs.module_system.global_registry import register_manifest as _register_manifest

_register_manifest(MODULE)
