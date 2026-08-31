"""app/composition/container.py — AppContainer: composition root con ciclo de vida explícito.

ADR-046: el composition root se construye UNA sola vez en el entry point
(CLI/MCP) o en un build diferido entre requests (reloader). El request solo
resuelve (resolve) y crea scopes efímeros (child) — nunca construye
infraestructura en el camino del request.

COMPOSICIÓN DECLARATIVA (NestJS, ADR-066/067): el container NO ensambla nada a
mano. Los módulos se AUTOREGISTRAN vía ModuleManifest (`app/module.py` declara los
INGREDIENTES del host: workflow_store, action_catalog, task_registry,
side_effect_engine — factories lazy por id+contrato). La INSTANCIA del engine la
construye `main` (register_all, §2.4) con parámetros explícitos; `_compose` solo
importa los módulos y usa el engine resuelto. No hay monkeypatch.

Reglas del container:
1. ``AppContainer.build()`` es el ÚNICO constructor del grafo.
2. ``resolve(name)`` no crea nada: devuelve instancias registradas en build().
3. ``child()`` es el único objeto efímero por request; los singletons del
   container no se tocan.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

from app.composition.errors import CompositionCycleError
from app.composition.import_graph import module_deps, tarjan
from app.composition.lifecycle import RequestScope, _CONTAINER, _inject_service_locator
from app.composition.registry import register_all


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
        from shared.core.engine.domain.execution import ExecutionContext

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
            from shared.core.engine.bootstrap import reset_module_registry

            reset_module_registry()
        except Exception:
            pass

    @classmethod
    def _validate_dag_or_fail(cls) -> None:
        """Validates that the composition graph is a DAG (ADR-052 D1).

        Builds a static import graph from loaded features.* modules using
        AST parsing (reuses module_deps from import_graph.py). Applies
        Tarjan's algorithm to find SCCs. If any SCC has len > 1, raises
        CompositionCycleError with the cycle modules.
        """
        loaded = {
            name: mod
            for name, mod in sys.modules.items()
            if (
                name.startswith("features.")
                or name.startswith("core.")
                or name.startswith("shared.")
            )
            and hasattr(mod, "__file__")
            and mod.__file__
        }
        graph = {name: module_deps(name, mod.__file__) for name, mod in loaded.items()}
        sccs = tarjan(graph)
        cycles = [sorted(scc) for scc in sccs if len(scc) > 1]
        if cycles:
            raise CompositionCycleError(
                f"Composition cycle detected: {cycles}. "
                "Fix: lazy exports (PEP 562), SSOT imports, or dynamic DI resolution."
            )

    @classmethod
    def _compose(cls, chain_dir: str = "") -> "AppContainer":
        """Construye el grafo completo (implementación interna de build()).

        Composición DECLARATIVA (estilo NestJS, ADR-066/067): NO hay glue
        manual ni monkeypatch. Los módulos se AUTOREGISTRAN vía ModuleManifest
        (app.module declara los providers del host: task_registry,
        side_effect_engine, action_catalog, engine, workflow) y el container
        solo resuelve por id+contrato desde el ModuleRegistry único del proceso.
        """
        from shared.libs.module_system.registry import module_registry

        # ADR-052 D1: Gate DAG de arranque — valida que el grafo de composición
        # sea un DAG antes de registrar. Si encuentra un SCC > 1, falla el
        # arranque listando los módulos del ciclo.
        cls._validate_dag_or_fail()

        # Único wiring: importa los módulos (cada uno se autoregistra) y
        # resuelve engine/workflow por id+contrato. Devuelve (orch, facade).
        from app.composition.registry import register_all

        orch, workflow_facade = register_all(module_registry=module_registry)
        module_registry.register_service("module_registry", module_registry)

        from features.project import ProjectService

        services: dict[str, Any] = {
            "module_registry": module_registry,
            "task_registry": module_registry.get_service("task_registry"),
            "side_effect_engine": module_registry.get_service("side_effect_engine"),
            "workflow": workflow_facade,
            "action_catalog": module_registry.get_service("action_catalog"),
            "project_service": ProjectService,
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
            task_registry=module_registry.get_service("task_registry"),
            side_effect_engine=module_registry.get_service("side_effect_engine"),
            module_registry=module_registry,
            services=services,
            chain_dir=chain_dir,
        )


__all__ = ["AppContainer"]
