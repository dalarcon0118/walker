"""shared — Paquete distribuible del Walker (taxonomía NestJS/DDD).

La antigua `shared/` se reorganizó en dos roles con responsabilidad declarada,
siguiendo la taxonomía de módulos de NestJS (Core vs Shared):

  shared/core/    → singletons inyectables (con estado o instancia única).
                     Se consumen por DI: get_module_service("clave").
                     NUNCA por import directo (regla "Core se inyecta").
                     Contiene: cognitive_graph, path_resolver, mcp_server,
                     cognitive_searcher, memories (fachada de stats).
  shared/common/  → puros importables (sin estado, sin IO).
                     Se consumen por import directo (regla "Shared se importa").
                     Contiene: shared_domain (VOs), result_types,
                     context (contratos DIP), router.

REGLA DE MÓDULO (definición formal):
  1. shared NO importa features/. Solo stdlib + shared entre sí (DIP).
  2. Cada paquete tiene UNA responsabilidad transversal (no lógica de negocio).
  3. Si algo pertenece a un feature (cpg, ckg, delta, trace, spring) o tiene
     lógica de negocio (transiciones, gates, schemas de tasks), vive en
     features/<feature>/ y NO en shared/. La lógica del workflow v4 legacy
     (antigua shared/infrastructure/utils.py) ya no vive en shared/ (se
     migró al feature correspondiente).
  4. Los events de dominio viven en features/<feature>/domain/events.py.
  5. Lo que se consume por DI (Core) se registra en el ModuleRegistry
     (features/engine/bootstrap.py) — nunca se importa directo.

================================================================================
DI: `shared` es un módulo consumible por otros por inyección de dependencias.
================================================================================
`shared` NO re-exporta clases concretas de sus componentes internos (eso acoplaría
al consumidor al detalle de implementación). En su lugar, `shared` POSEE un
`ModuleRegistry` y, al importarse, registra sus componentes internos **por id**
(estilo ADR-062: id + contrato). Los consumidores resuelven por id+contrato:

    from shared import shared_registry
    engine_module = shared_registry.get_module("shared.engine")        # por id
    state_store   = shared_registry.resolve_port(IStateStore)          # por contrato
    engine_api     = shared_registry.get_service("shared.engine")      # por id

El composition root (app/composition/registry.py) llama a `register_shared_components`
para mezclar el registro de `shared` en su propio `ModuleRegistry` (única fuente de
DI para la app). `shared` es host-agnóstico (INV-015): solo registra adaptadores por
defecto que NO asumen el host; los que dependen del host (IProjectContext,
IConflictResolver, ISprintService, IPromptCollector, IConfig con rutas reales) los
registra el composition root.
"""

from __future__ import annotations

from shared.libs.module_system.contracts import ModuleDefinition
from shared.libs.module_system.registry import (
    InMemoryModuleRegistry,
    module_registry,
    reset_module_registry,
)

# DI: `shared` se autoregistra en el contenedor único del proceso. Los consumidores
# (incluida la app) resuelven por id+contrato desde `module_registry`. `shared_registry`
# es un alias de legibilidad hacia ese mismo contenedor.
shared_registry = module_registry


def register_shared_components(registry: InMemoryModuleRegistry) -> None:
    """Registra los componentes internos de `shared` en un ModuleRegistry por id.

    Idempotente. Registra:
      - módulo "shared.engine" (por id) con sus contratos en metadata.
      - servicio "shared.engine" (la fachada pública del motor).
      - adaptadores por defecto host-agnósticos por contrato (los que el motor
        mismo posee). Los puertos que dependen del host los registra el composition
        root (no aquí).
    """
    from shared.core.engine.domain.ports import (
        IStateStore,
        IConfig,
        IContextValidator,
        ISideEffectEngine,
        IWorkflowIO,
        IWorkflowLoader,
        IPresenter,
        IWorkflowStore,
        IProjectContext,
        IConflictResolver,
        ISprintService,
        IPromptCollector,
    )
    from shared.core.engine.domain.ports_registry import resolve_port as _resolve_engine_port
    from shared.core.engine.infrastructure.repositories import (
        WorkflowRepository,
        WorkflowStateRepository,
    )
    from shared.core.engine.infrastructure.workflow_store import WorkflowStoreAdapter
    from shared.core.engine.infrastructure.config import ConfigAdapter
    from shared.core.engine.application.context_resolver import ContextResolver
    from shared.core.engine.infrastructure.side_effects import SideEffectEngine
    import shared.core.engine as engine

    # 1) Módulo por id (publicita los contratos que el motor expone).
    registry.register_module(
        ModuleDefinition(
            name="shared.engine",
            tools=[],
            routes=[],
            metadata={
                "role": "workflow-engine",
                "contracts": [
                    "IStateStore",
                    "IConfig",
                    "IContextValidator",
                    "ISideEffectEngine",
                    "IWorkflowIO",
                    "IWorkflowLoader",
                    "IPresenter",
                    "IProjectContext",
                    "IConflictResolver",
                    "ISprintService",
                    "IPromptCollector",
                ],
            },
        )
    )

    # 2) Servicio por id (fachada pública del motor).
    registry.register_service("shared.engine", engine)

    # 3) Providers internos del motor, registrados por `shared` (NestJS-like):
    # el módulo publica sus propios servicios para que el orquestador los resuelva
    # por id desde el contenedor, sin instanciar concreto alguno en el consumidor.
    registry.register_service(
        "workflow_repo",
        WorkflowRepository(
            workflow_path=str(_resolve_engine_port(IConfig).get("workflow.manifest"))
        ),
    )
    registry.register_service("state_repo", WorkflowStateRepository())

    # 4) Adaptadores por defecto host-agnósticos (por contrato).
    registry.register_port(IStateStore, WorkflowStateRepository())
    registry.register_port(IConfig, ConfigAdapter())
    registry.register_port(IContextValidator, ContextResolver())
    registry.register_port(ISideEffectEngine, SideEffectEngine())
    registry.register_port(IWorkflowIO, _EngineWorkflowIO())
    registry.register_port(IWorkflowLoader, _EngineWorkflowLoader())
    registry.register_port(IPresenter, _EnginePresenter())
    registry.register_port(IWorkflowStore, WorkflowStoreAdapter())


class _EngineWorkflowIO:
    """Adaptador por defecto de IWorkflowIO (host-agnóstico: usa subprocess stdlib).

    Contrato de retorno: {"exit_code", "stdout", "stderr"} (idéntico a ScriptRunner)
    para que los casos de uso del engine lo consuman sin cambios.
    """

    def run_script(self, script: str, chain_dir: str | None = None, task_id: str | None = None) -> dict[str, object]:
        import subprocess

        proc = subprocess.run(script, shell=True, capture_output=True, text=True, cwd=chain_dir)
        return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}

    def eprint(self, *args: object, **kwargs: object) -> None:
        import sys

        print(*args, file=sys.stderr, **kwargs)

    def get_sub_state(self, workflow, node_id: str, sub_key: str):  # type: ignore[no-untyped-def]
        node = workflow.nodes.get(node_id) if hasattr(workflow, "nodes") else None
        if node is None:
            return None
        for ss in getattr(node, "sub_states", []) or []:
            if getattr(ss, "key", None) == sub_key:
                return ss
        return None


class _EngineWorkflowLoader:
    """Adaptador por defecto de IWorkflowLoader basado en la fachada del engine."""

    def load_workflow(self, workflow_repo):  # type: ignore[no-untyped-def]
        return workflow_repo.load()

    def load_state(self, chain_dir: str):
        from shared.core.engine import load_state

        return load_state(chain_dir)

    def save_state(self, state, chain_dir: str) -> None:  # type: ignore[no-untyped-def]
        from shared.core.engine import save_state

        save_state(state, chain_dir)


class _EnginePresenter:
    """Adaptador por defecto de IPresenter (no-op; el host provee el real)."""

    def show_section(self, title: str) -> None:
        print(title)

    def show_node_header(self, current: str, node, sub_state=None) -> None:  # type: ignore[no-untyped-def]
        print(f"[{current}] {getattr(node, 'node_id', current)}")

    def show_transitions(self, workflow, node_id: str, chain_dir: str | None = None) -> None:  # type: ignore[no-untyped-def]
        print(f"transitions of {node_id}")

    def show_flow_progress(self, state) -> None:  # type: ignore[no-untyped-def]
        print(f"progress: {getattr(state, 'current_node', '?')}")


# Poblar el contenedor DI único al importar `shared` (DISPARA el autoregistro del
# módulo "shared.engine" por id + adaptadores por defecto por contrato). NestJS-like:
# el módulo se autoregistra; el DI lo localiza e inyecta. El composition root NO
# registra `shared`.
register_shared_components(module_registry)


# ── Exportación pública del motor (patrón servidor) ─────────────────────────────
# `shared` NO instancia el motor ni sabe quién lo usa: lo EXPORTA hacia afuera para
# que el HOST (main/app) lo instancie inyectándole TODAS sus dependencias:
#
#     from shared import Engine
#     engine = Engine(workflow_repo=..., state_repo=..., workflow_io=...,
#                     task_registry=..., side_effect_engine=...)
#
# Así el motor es un paquete normal: conocida su configuración, el host crea una
# instancia de workflow lista para ejecutarse (INV-015 / ADR-WALKER-067).
from shared.core.engine.application.orchestrator import OrchestrationService as Engine
