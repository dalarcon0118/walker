"""shared/libs/module_system/contracts.py — Contratos del sistema de módulos (ADR-066).

Genérico y reutilizable: NO pertenece al motor de workflow (shared/core/engine),
que queda como dominio puro (state machine, gates, TaskRegistry, ActionCatalog).
Cualquier capa (features, protocols, app) declara su module.py usando
ModuleManifest y se compone vía InMemoryModuleRegistry.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


class ToolDefinition:
    """Definición de una tool MCP con contrato JSON Schema.

    SSOT del contrato (ADR-WALKER-043): input_schema y output_schema se definen
    UNA sola vez (normalmente en el registro de la task) y las capas de
    exposición (MCP, REST, adapters) los derivan — nunca los re-escriben.
    """
    def __init__(self, name: str, description: str, handler: Any, input_schema: dict | None = None, metadata: dict | None = None, output_schema: dict | None = None):
        self.name = name
        self.description = description
        self.handler = handler
        self.input_schema = input_schema or {"type": "object", "properties": {}}
        self.output_schema = output_schema or {}
        self.metadata = metadata or {}


class RestRoute:
    """Definición de una ruta REST."""
    def __init__(self, path: str, method: str = "POST", flags_builder: Any = None):
        self.path = path
        self.method = method
        self.flags_builder = flags_builder or (lambda a: {"_payload": a})


class ModuleDefinition:
    """Definición de un módulo composite."""
    def __init__(self, name: str, tools: list[ToolDefinition] | None = None, routes: list[RestRoute] | None = None, metadata: dict | None = None):
        self.name = name
        self.tools = tools or []
        self.routes = routes or []
        self.metadata = metadata or {}


@dataclass
class ModuleManifest:
    """Manifiesto declarativo de un módulo (estilo NestJS @Module, ADR-066).

    Cada path de las 5 capas (ADR-065) declara en su module.py un ModuleManifest
    con imports/providers/ports/adapters/exports/tools/routes/register. El
    composition root los agrega y el IModuleRegistry aplica la DI (register_manifest).
    """
    name: str
    imports: list[str] = field(default_factory=list)
    providers: list[type] = field(default_factory=list)
    ports: list[type] = field(default_factory=list)
    adapters: list[tuple[type, type]] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    tools: list[ToolDefinition] = field(default_factory=list)
    routes: list[RestRoute] = field(default_factory=list)
    register: Callable | None = None


class IModule(ABC):
    """Interfaz de módulo composite auto-registrable."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def tools(self) -> list[ToolDefinition]:
        ...

    @abstractmethod
    def routes(self) -> list[RestRoute]:
        ...

    @abstractmethod
    def metadata(self) -> dict:
        ...


class IModuleRegistry(ABC):
    """Contrato del registry de módulos composite + container de servicios (DI).

    Dos responsabilidades claras:
      - Módulos: registra/resuelve módulos composite auto-registrables.
      - Servicios (DI): registra servicios compartidos de shared/ por clave lógica.
        Los features resuelven por clave (get_service) en vez de importar
        implementaciones concretas de shared/ directo (inversión de control).
    """

    # ── Módulos ──────────────────────────────────────────────────────────
    @abstractmethod
    def register_module(self, module: IModule | ModuleDefinition) -> None:
        ...

    @abstractmethod
    def get_module(self, name: str) -> IModule | None:
        ...

    @abstractmethod
    def list_modules(self) -> list[str]:
        ...

    @abstractmethod
    def all_tools(self) -> list[ToolDefinition]:
        ...

    @abstractmethod
    def all_routes(self) -> list[RestRoute]:
        ...

    @abstractmethod
    def tool(self, name: str) -> ToolDefinition | None:
        ...

    @abstractmethod
    def route(self, name: str) -> RestRoute | None:
        ...

    # ── Servicios (DI / IoC) ─────────────────────────────────────────────
    @abstractmethod
    def register_service(self, name: str, instance: Any, *, lazy: bool = False) -> None:
        ...

    @abstractmethod
    def get_service(self, name: str) -> Any:
        ...

    @abstractmethod
    def has_service(self, name: str) -> bool:
        ...

    @abstractmethod
    def list_services(self) -> list[str]:
        ...


__all__ = [
    "ToolDefinition",
    "RestRoute",
    "ModuleDefinition",
    "ModuleManifest",
    "IModule",
    "IModuleRegistry",
]
