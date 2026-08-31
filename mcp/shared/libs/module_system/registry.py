"""shared/libs/module_system/registry.py — Implementación in-memory de ModuleRegistry.

Container de servicios compartidos (DI) + registro de módulos composite. Los
adapters de shared/ se registran por clave lógica y los features los resuelven
por get_service() en vez de importar implementaciones concretas (ADR-066).
"""
from __future__ import annotations

from typing import Any

from shared.libs.module_system.contracts import (
    IModule,
    IModuleRegistry,
    ModuleDefinition,
    ModuleManifest,
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
        self._manifests: dict[str, ModuleManifest] = {}
        self._port_adapters: dict[type, type] = {}

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
            instance = factory()
            self._services[name] = instance
            return instance
        raise KeyError(f"Servicio no registrado: {name}")

    def has_service(self, name: str) -> bool:
        return name in self._services or name in self._service_factories

    def list_services(self) -> list[str]:
        return sorted(set(self._services) | set(self._service_factories))

    # ── ModuleManifest (ADR-066) ──────────────────────────────────────────

    def register_manifest(self, m: ModuleManifest) -> None:
        """Aplica un ModuleManifest declarativo: acyclicidad, adapters, providers, register()."""
        self._manifests[m.name] = m
        self._check_acyclic()
        for prov in m.providers:
            self.register_service(prov.__name__, prov, lazy=True)
        for port, impl in m.adapters:
            self._port_adapters[port] = impl
        if callable(m.register):
            m.register(self)

    def _check_acyclic(self) -> None:
        """Valida que el grafo de `imports` entre módulos no tenga ciclos."""
        graph = {n: m.imports for n, m in self._manifests.items()}
        color: dict[str, int] = {n: 0 for n in graph}

        def dfs(u: str, path: list[str]) -> None:
            color[u] = 1
            for v in graph.get(u, []):
                if v not in graph:
                    continue  # importa a módulo aún no registrado: se valida al registrarlo
                if color.get(v) == 1:
                    raise ValueError(f"Ciclo en imports de módulos: {path + [u, v]}")
                if color.get(v) == 0:
                    dfs(v, path + [u])
            color[u] = 2

        for n in graph:
            if color[n] == 0:
                dfs(n, [])

    def register_port(self, port: type, impl: Any) -> None:
        """Registra un adapter (clase o instancia) para un puerto del dominio.

        Si se registra una *clase*, ``resolve_port`` la instancia inyectándole las
        dependencias declaradas en ``__init__`` por nombre (ADR-066 F5: DI por
        id+contrato). Si se registra una *instancia*, ``resolve_port`` la devuelve
        directamente (singleton compartido). Esto permite tanto manifests
        declarativos como autoregistro de módulos (NestJS-like) que registran sus
        adapters por contrato al importarse.
        """
        self._port_adapters[port] = impl

    def resolve_port(self, port: type) -> Any:
        """Resuelve un puerto del dominio, inyectando dependencias por nombre
        (ADR-066 F5: DI por id+contrato). Acepta adapters registrados como clase
        (se instancia con sus deps) o como instancia (se devuelve tal cual).
        """
        import inspect

        impl = self._port_adapters.get(port)
        if impl is None:
            raise KeyError(f"Puerto no registrado en ningún manifest: {port}")
        if inspect.isclass(impl):
            sig = inspect.signature(impl.__init__)
            kwargs: dict[str, Any] = {}
            for name in sig.parameters:
                if name == "self":
                    continue
                kwargs[name] = self.get_service(name)
            return impl(**kwargs)
        return impl


# ── Contenedor DI único del proceso (NestJS-like) ──────────────────────────────
# Los módulos (p.ej. ``shared``) se AUTOREGISTRAN aquí al importarse; el
# composition root NO registra los módulos de shared, solo los providers
# dependientes del host. Cualquier consumidor (otra app, otra libre) resuelve
# por id+contrato desde este mismo contenedor. ``reset_module_registry`` reinicia
# el contenedor para aislar tests.
module_registry: "InMemoryModuleRegistry" = InMemoryModuleRegistry()


def reset_module_registry() -> None:
    """Reinicia el contenedor DI del proceso (uso en tests para aislar estado)."""
    global module_registry
    module_registry = InMemoryModuleRegistry()
