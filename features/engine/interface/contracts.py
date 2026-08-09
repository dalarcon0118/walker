"""interface/contracts.py — Contratos públicos del engine.

Define las interfaces y DTOs que el engine expone al exterior.
Los consumidores dependen de estos contratos, no de implementaciones.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from features.engine.domain.models import WorkflowState, WorkflowGraph


# ── DTOs de dominio (MCP/REST) ──────────────────────────────────────────────


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


# ── Interfaces abstractas ───────────────────────────────────────────────────


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
      - Servicios (DI): registra servicios compartidos de libs/ por clave lógica.
        Los features resuelven por clave (get_service) en vez de importar
        implementaciones concretas de libs/ directo (inversión de control).
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
        """Registra un servicio bajo una clave lógica.

        Args:
            name: Clave lógica (ej: "cognitive_searcher", "script_runner").
            instance: Instancia del servicio. Si lazy=True y provider es
                      callable, se evalúa en el primer get_service() y se
                      cachea (factory singleton).
        """
        ...

    @abstractmethod
    def get_service(self, name: str) -> Any:
        """Resuelve un servicio registrado. KeyError si no existe."""
        ...

    @abstractmethod
    def has_service(self, name: str) -> bool:
        """¿Existe el servicio bajo la clave dada?"""
        ...

    @abstractmethod
    def list_services(self) -> list[str]:
        """Claves de todos los servicios registrados."""
        ...


class IWorkflowEngine:
    """Contrato del motor de workflow."""

    def load_workflow(self, chain_dir: str) -> WorkflowGraph:
        ...

    def load_state(self, chain_dir: str) -> WorkflowState:
        ...

    def save_state(self, state: WorkflowState, chain_dir: str) -> None:
        ...

    def advance_sub_state(self, state: WorkflowState, graph: WorkflowGraph) -> WorkflowState:
        ...

    def validate_gate(self, workflow: dict, node_id: str, gate: str, chain_dir: str | None = None, task_id: str | None = None) -> dict:
        ...


class ITaskDispatcher:
    """Contrato del dispatcher de tasks públicas."""

    def dispatch(self, name: str, body: dict) -> dict:
        ...


__all__ = [
    "ToolDefinition",
    "RestRoute",
    "ModuleDefinition",
    "IModule",
    "IModuleRegistry",
    "IWorkflowEngine",
    "ITaskDispatcher",
]
