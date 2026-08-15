"""features/main/composition/container.py — AppContainer: composition root con ciclo de vida explícito.

ADR-046: el composition root se construye UNA sola vez en el entry point
(CLI/MCP) o en un build diferido entre requests (reloader). El request solo
resuelve (resolve) y crea scopes efímeros (child) — nunca construye
infraestructura en el camino del request.

Evoluciona AppComposition (dataclass pasiva) y absorbe el singleton de
composición como única fuente del grafo. core/engine sigue sin conocer
features/ (el resolutor del service locator se inyecta, no se importa).

Reglas del container:
1. ``AppContainer.build()`` es el ÚNICO constructor del grafo.
2. ``resolve(name)`` no crea nada: devuelve instancias registradas en build().
3. ``child()`` es el único objeto efímero por request; los singletons del
   container no se tocan.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from features.main.composition.lifecycle import RequestScope, _CONTAINER, _inject_service_locator
from features.main.composition.registry import register_all


@dataclass
class AppContainer:
    """Composition root con ciclo de vida explícito (ADR-046).

    - Se construye UNA vez por proceso (entry point) o en un build diferido.
    - resolve(name): SOLO lectura, devuelve instancias registradas en build().
    - child(chain_dir, task_id): scope de un request (ctx + chain_dir + task_id).
    """

    def __init__(
        self,
        *,
        workflow: Any,
        task_registry: Any,
        side_effect_engine: Any,
        module_registry: Any = None,
        services: dict[str, Any] | None = None,
        workflow_graph: Any = None,
        chain_dir: str = "",
    ) -> None:
        self.workflow = workflow
        self.task_registry = task_registry
        self.side_effect_engine = side_effect_engine
        self.module_registry = module_registry
        self.workflow_graph = workflow_graph
        self._chain_dir = chain_dir
        # DI por clave lógica: instancias/factories registradas en build().
        self._services: dict[str, Any] = dict(services or {})

    # ── DI por clave lógica (reemplaza get_module_service) ──

    def resolve(self, name: str) -> Any:
        """SOLO lectura: devuelve un servicio ya registrado en build().

        Delega en el ModuleRegistry (que maneja instancias eager y factories
        lazy ya cacheadas). No construye infraestructura de composición:
        si el servicio no existe, KeyError.
        """
        if self.module_registry is not None and self.module_registry.has_service(name):
            return self.module_registry.get_service(name)
        try:
            return self._services[name]
        except KeyError:
            raise KeyError(f"Servicio no registrado: {name}") from None

    # ── Scope de request (contexto efímero) ──

    def child(self, chain_dir: str = "", task_id: str = "") -> RequestScope:
        """Scope de un request: snapshot ligero (ctx + chain_dir + task_id).

        No construye nada: solo captura referencias ya existentes.
        """
        from shared.common.shared_domain import ExecutionContext

        ctx = ExecutionContext.create(
            session_id=str(chain_dir).split("/")[-1] if chain_dir else "",
            chain_dir=chain_dir,
            task_id=task_id or "",
        )
        return RequestScope(container=self, chain_dir=chain_dir, task_id=task_id, ctx=ctx)

    # ── Ciclo de vida ──

    @classmethod
    def build(cls, chain_dir: str = "", *, force: bool = False) -> "AppContainer":
        """ÚNICA construcción del grafo (cold start, fuera del request).

        - force=False: singleton estable — todos los consumidores comparten
          el MISMO container/module_registry (capa_rout.md D1).
        - force=True: build diferido (reloader, ADR-046 Fase 3): construye un
          container NUEVO y lo swappea SOLO si el build es exitoso; en fallo
          se conserva el container actual (nunca degrade).
        """
        global _CONTAINER
        if _CONTAINER is not None and not force:
            _inject_service_locator(_CONTAINER)
            return _CONTAINER
        container = cls._compose(chain_dir)
        _CONTAINER = container
        _inject_service_locator(container)
        return container

    @classmethod
    def reset(cls) -> None:
        """Invalida el container (solo tests / hot reload forzado)."""
        global _CONTAINER
        _CONTAINER = None
        try:
            from core.engine.bootstrap import reset_module_registry

            reset_module_registry()
        except Exception:
            pass

    @classmethod
    def _compose(cls, chain_dir: str = "") -> "AppContainer":
        """Construye el grafo completo (implementación interna de build())."""
        # 1. Infraestructura base del engine
        from features.main.composition.registry import _build_tracer_core

        tracer = _build_tracer_core()
        from core.engine.infrastructure.task_registry import InMemoryTaskRegistry
        from core.engine.infrastructure.side_effects import SideEffectEngine
        from core.engine.infrastructure.repositories import WorkflowRepository, WorkflowStateRepository, ScriptRunner

        registry = InMemoryTaskRegistry(tracer=tracer)
        se_engine = SideEffectEngine(tracer=tracer)

        from shared.core.path_resolver import get_engine_workflow_path
        from features.project import ProjectService

        workflow_path = str(get_engine_workflow_path())
        if chain_dir:
            try:
                # Replicar la lógica de resolución del WorkflowRepository anterior:
                # 1. Buscar en parent/workflow.v3/workflow.json (relativo al chain_dir)
                parent = os.path.dirname(os.path.dirname(chain_dir))
                wf_dir = 'workflow.v3'
                modular = os.path.join(parent, wf_dir, 'workflow.json')
                if os.path.exists(modular):
                    workflow_path = modular
                else:
                    # 2. Buscar en project_root/workflow.v3/workflow.json
                    paths = ProjectService.resolve_paths(chain_dir)
                    project_wf = os.path.join(
                        str(paths.project_root), wf_dir, 'workflow.json'
                    )
                    if os.path.exists(project_wf):
                        workflow_path = project_wf
            except Exception:
                pass
        workflow_repo = WorkflowRepository(workflow_path=workflow_path)

        # 1a. ContextProviderRegistry completo: base del engine (task, state,
        # diff, test_results) + providers de negocio de features (business,
        # ckg, cpg, delta, project, reflection, trace). ADR-045: el
        # composition root es el ÚNICO que arma este registry.
        from core.engine.bootstrap import build_context_registry

        context_registry = build_context_registry()

        # 3. ModuleRegistry unificado (composition root final, ADR-045)
        from core.engine.infrastructure.module_registry import InMemoryModuleRegistry
        from features.main.composition.registry import _build_workflow_facade

        module_registry = InMemoryModuleRegistry()

        # 4. OrchestrationService sin action_catalog todavía (se inyecta después
        # de register_all para romper el ciclo orch → facade → register_all).
        from core.engine.application.orchestrator import OrchestrationService

        orch = OrchestrationService(
            workflow_repo=workflow_repo,
            state_repo=WorkflowStateRepository(),
            script_runner=ScriptRunner(),
            task_registry=registry,
            side_effect_engine=se_engine,
            tracer=tracer,
        )

        # 5. WorkflowFacade (depende de orch)
        workflow_facade = _build_workflow_facade(orch)

        # 6. Registro declarativo idempotente (ADR-046 Fase 2)
        register_all(
            context_registry=context_registry,
            task_registry=registry,
            side_effect_engine=se_engine,
            module_registry=module_registry,
            workflow_facade=workflow_facade,
        )

        # 7. ActionCatalog: el puerto único de workflow actions (SPEC-014
        # Fase 1). Se construye DESPUÉS de register_all para tener todos
        # los handlers registrados.
        from core.engine.domain.workflow_action import (
            ActionCatalog,
            KIND_SIDE_EFFECT,
            KIND_SUB_STATE_ACTION,
        )

        action_catalog = ActionCatalog()
        for meta in registry.list_tasks_with_meta(scope=None):
            action_catalog.register(
                name=meta["name"],
                kind=KIND_SUB_STATE_ACTION,
                scope=meta.get("scope", "public"),
                handler=registry.get_task(meta["name"]),
                metadata=meta,
            )
        for name, handler in se_engine._handlers.items():
            action_catalog.register(
                name=name,
                kind=KIND_SIDE_EFFECT,
                scope="internal",
                handler=handler,
                metadata={"source": "side_effect_engine"},
            )

        # 8. Inyectar action_catalog en orch y se_engine (SPEC-014 F1)
        orch._action_catalog = action_catalog
        se_engine._action_catalog = action_catalog

        # 4. DI por clave lógica: las instancias ya construidas en build().
        services: dict[str, Any] = {
            "module_registry": module_registry,
            "task_registry": registry,
            "side_effect_engine": se_engine,
            "workflow": workflow_facade,
            "action_catalog": action_catalog,
        }
        for key in (
            "cognitive_searcher",
            "cognitive_graph",
            "path_resolver",
            "prompt_manager",
        ):
            if module_registry.has_service(key):
                services[key] = module_registry.get_service(key)

        return cls(
            workflow=orch,
            task_registry=registry,
            side_effect_engine=se_engine,
            module_registry=module_registry,
            services=services,
            chain_dir=chain_dir,
        )


__all__ = ["AppContainer"]
