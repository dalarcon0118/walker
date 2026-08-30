"""shared/core/engine/application/bundle.py — VO con TODAS las dependencias del motor.

Patrón servidor / constructor injection (ADR-WALKER-067): el motor es anémico y
NO acopla a ningún service locator, contenedor DI ni arquitectura externa. El HOST
(main/app) construye este VO reuniendo las implementaciones concretas (tasks,
side-effects, IO, repos, y los puertos dependientes del host) y se lo pasa al
motor vía `workflow_api`. El motor solo consume el VO; no resuelve nada por sí solo.

El VO es el contrato de entrada del engine: conocida esta configuración, el host
crea una instancia de workflow lista para ejecutarse.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain.ports import (
    IWorkflowIO,
    IWorkflowStore,
    ITracer,
)
from ..domain.workflow_action import ActionCatalog


@dataclass
class EngineBundle:
    """Value Object con todo lo que el motor necesita para ejecutarse.

    UNIFICACIÓN REAL (ADR-WALKER-067 §7.2) — SIN parámetros redundantes:
      - `workflow_store` (IWorkflowStore: load_graph/load_state/save_state)
        reemplaza workflow_repo + state_repo.
      - `actions` (ActionCatalog: execute_action) reemplaza task_registry +
        side_effect_engine + action_catalog. El Saga de side-effects lo
        orquesta el engine vía actions (SideEffectSaga).

    Campos requeridos: el host debe proveerlos (el motor falla fuerte si no).
    """

    # ── Requeridos (el motor no arranca sin ellos) ──
    workflow_store: IWorkflowStore
    actions: ActionCatalog
    workflow_io: IWorkflowIO

    # ── Opcionales (mecánica interna del engine) ──
    context_registry: Any = None
    contract_validator: Any = None
    event_dispatcher: Any = None
    event_log: Any = None
    tracer: ITracer | None = None

    def to_orchestrator_kwargs(self) -> dict[str, Any]:
        """Expande el VO en los argumentos del OrchestrationService (server config)."""
        return {
            "workflow_store": self.workflow_store,
            "actions": self.actions,
            "workflow_io": self.workflow_io,
            "context_registry": self.context_registry,
            "contract_validator": self.contract_validator,
            "event_dispatcher": self.event_dispatcher,
            "event_log": self.event_log,
            "tracer": self.tracer,
        }
