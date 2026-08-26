"""
infrastructure/module_registry.py — Implementación in-memory de ModuleRegistry.

Además de módulos composite, actúa como container de servicios compartidos
(DI): los adapters de shared/ se registran por clave lógica y los features los
resuelven por get_service() en vez de importar implementaciones concretas.
"""

from __future__ import annotations

from typing import Any

from shared.core.engine.domain.module import (
    IModule,
    IModuleRegistry,
)
from shared.core.engine.interface.contracts import (
    ModuleDefinition,
    RestRoute,
    ToolDefinition,
)


class InMemoryModuleRegistry(IModuleRegistry):
    """Registry in-memory de módulos composite + servicios DI."""

    def __init__(self):
        self._modules: dict[str, IModule | ModuleDefinition] = {}
        self._tools: dict[str, ToolDefinition] = {}
        self._routes: dict[str, RestRoute] = {}
        self._services: dict[str, Any] = {}
        self._service_factories: dict[str, Any] = {}

    def register_module(self, module: IModule | ModuleDefinition) -> None:
        if isinstance(module, IModule):
            name = module.name
            tools = module.tools()
            routes = module.routes()
            module.metadata()
        else:
            name = module.name
            tools = module.tools
            routes = module.routes

        self._modules[name] = module
        for tool in tools:
            self._tools[tool.name] = tool
        for route in routes:
            self._routes[route.path] = route

    def get_module(self, name: str) -> IModule | None:
        return self._modules.get(name)

    def list_modules(self) -> list[str]:
        return list(self._modules.keys())

    def all_tools(self) -> list[ToolDefinition]:
        seen = set()
        result = []
        for tool in self._tools.values():
            if tool.name not in seen:
                seen.add(tool.name)
                result.append(tool)
        return result

    def all_routes(self) -> list[RestRoute]:
        seen = set()
        result = []
        for route in self._routes.values():
            if route.path not in seen:
                seen.add(route.path)
                result.append(route)
        return result

    def tool(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def route(self, name: str) -> RestRoute | None:
        key = f"{{project_id}}/{name}"
        route = self._routes.get(key)
        if route is None:
            # Tools con ruta bare (list/help/prepare): se registran sin prefijo
            # {project_id}/. Fallback al path plano para que el MCP dispatch
            # las resuelva igual que las scoped (status/submit/transition).
            route = self._routes.get(name)
        return route

    # ── Servicios (DI / IoC) ─────────────────────────────────────────────

    def register_service(self, name: str, instance: Any, *, lazy: bool = False) -> None:
        if lazy and callable(instance):
            self._service_factories[name] = instance
        else:
            self._services[name] = instance

    def get_service(self, name: str) -> Any:
        if name in self._services:
            return self._services[name]
        factory = self._service_factories.get(name)
        if factory is not None:
            instance = factory()  # singleton lazy: construye una vez y cachea
            self._services[name] = instance
            return instance
        raise KeyError(f"Servicio no registrado: {name}")

    def has_service(self, name: str) -> bool:
        return name in self._services or name in self._service_factories

    def list_services(self) -> list[str]:
        return sorted(set(self._services) | set(self._service_factories))
