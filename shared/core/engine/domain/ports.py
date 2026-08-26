"""domain/ports.py — Puertos (interfaces) del dominio Workflow Navigation.

PRINCIPIO: El dominio define QUÉ necesita, no CÓMO se implementa.
Los adaptadores de infrastructure implementan estos puertos.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from shared.core.engine.domain.entities import WorkflowGraph, WorkflowState


class IWorkflowIO(ABC):
    """Puerto para operaciones de IO del workflow.

    Abstrae:
      - Ejecución de scripts (gate validation, auto_complete)
      - Output a stderr
      - Acceso a sub-estados en workflow dicts
    """

    @abstractmethod
    def run_script(self, script: str, chain_dir: str | None = None, task_id: str | None = None) -> dict[str, Any]:
        """Ejecuta un script shell y retorna resultado."""
        ...

    @abstractmethod
    def eprint(self, *args: Any, **kwargs: Any) -> None:
        """Imprime a stderr."""
        ...

    @abstractmethod
    def get_sub_state(self, workflow: dict, node_id: str, sub_key: str) -> dict | None:
        """Obtiene un sub-estado del workflow dict."""
        ...




class IProjectContext(ABC):
    """Puerto para acceso al contexto del proyecto (paths, metadata).

    El engine NO conoce features.project. Solo necesita resolver rutas
    del proyecto. El main/project_service implementa este puerto.
    """

    @abstractmethod
    def resolve_paths(self, chain_dir: str) -> Any:
        """Resuelve paths del proyecto desde chain_dir."""
        ...

    @abstractmethod
    def get_workflow_json_path(self, chain_dir: str) -> str:
        """Retorna la ruta a workflow.json del proyecto."""
        ...

    @abstractmethod
    def list_available(self) -> list[dict]:
        """Lista proyectos disponibles."""
        ...

    @abstractmethod
    def find_active_sprint(self, project_id: str) -> str:
        """Busca el sprint activo del proyecto."""
        ...


class IConflictResolver(ABC):
    """Puerto para resolución de conflictos del workflow.

    El engine NO conoce delta_tracking. Solo necesita resolver conflictos.
    El main/delta_tracking implementa este puerto.
    """

    @abstractmethod
    def resolve(
        self,
        conflict: Any,
        strategy: str,
        payload: dict[str, Any],
        workflow_state: dict[str, Any],
        specs: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Resuelve un conflicto aplicando la estrategia indicada."""
        ...


class ISprintService(ABC):
    """Puerto para acceso al sprint/spring del workflow.

    El engine NO conoce delta_tracking. Solo necesita consultar/actualizar
    el estado del sprint. El main/delta_tracking implementa este puerto.
    """

    @abstractmethod
    def from_chain_dir(self, chain_dir: str) -> Any:
        """Construye el servicio desde chain_dir."""
        ...

    @abstractmethod
    def load_tasks(self, chain_dir: str) -> list[dict]:
        """Carga las tasks del sprint."""
        ...

    @abstractmethod
    def get_spring(self, spring_id: str) -> dict | None:
        """Obtiene un spring por ID."""
        ...


class IPromptCollector(ABC):
    """Puerto para recolección de prompts/contexto.

    El engine NO conoce features.main.prompts. Solo necesita recolectar
    prompts. El main implementa este puerto.
    """

    @abstractmethod
    def collect(self, chain_dir: str, context: dict[str, Any]) -> dict[str, Any]:
        """Recolecta prompts para el contexto."""
        ...
class IWorkflowLoader(ABC):
    """Puerto para carga de workflow y estado desde disco."""

    @abstractmethod
    def load_workflow(self, workflow_repo: 'WorkflowRepository') -> WorkflowGraph:
        ...

    @abstractmethod
    def load_state(self, chain_dir: str) -> WorkflowState:
        ...

    @abstractmethod
    def save_state(self, state: WorkflowState, chain_dir: str) -> None:
        ...


class IPresenter(ABC):
    """Puerto para presentación de estado del workflow."""

    @abstractmethod
    def show_section(self, title: str) -> None:
        ...

    @abstractmethod
    def show_node_header(self, current: str, node: dict, sub_state: dict | None = None) -> None:
        ...

    @abstractmethod
    def show_transitions(self, workflow: dict, node_id: str, chain_dir: str | None = None) -> None:
        ...

    @abstractmethod
    def show_flow_progress(self, state: dict) -> None:
        ...


class IProjectContext(ABC):
    """Puerto para acceso al contexto del proyecto (paths, metadata).

    El engine NO conoce features.project. Solo necesita resolver rutas
    del proyecto. El main/project_service implementa este puerto.
    """

    @abstractmethod
    def resolve_paths(self, chain_dir: str) -> Any:
        """Resuelve paths del proyecto desde chain_dir."""
        ...

    @abstractmethod
    def get_workflow_json_path(self, chain_dir: str) -> str:
        """Retorna la ruta a workflow.json del proyecto."""
        ...

    @abstractmethod
    def list_available(self) -> list[dict]:
        """Lista proyectos disponibles."""
        ...

    @abstractmethod
    def find_active_sprint(self, project_id: str) -> str:
        """Busca el sprint activo del proyecto."""
        ...


class IConflictResolver(ABC):
    """Puerto para resolución de conflictos del workflow.

    El engine NO conoce delta_tracking. Solo necesita resolver conflictos.
    El main/delta_tracking implementa este puerto.
    """

    @abstractmethod
    def resolve(
        self,
        conflict: Any,
        strategy: str,
        payload: dict[str, Any],
        workflow_state: dict[str, Any],
        specs: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Resuelve un conflicto aplicando la estrategia indicada."""
        ...


class ISprintService(ABC):
    """Puerto para acceso al sprint/spring del workflow.

    El engine NO conoce delta_tracking. Solo necesita consultar/actualizar
    el estado del sprint. El main/delta_tracking implementa este puerto.
    """

    @abstractmethod
    def from_chain_dir(self, chain_dir: str) -> Any:
        """Construye el servicio desde chain_dir."""
        ...

    @abstractmethod
    def load_tasks(self, chain_dir: str) -> list[dict]:
        """Carga las tasks del sprint."""
        ...

    @abstractmethod
    def get_spring(self, spring_id: str) -> dict | None:
        """Obtiene un spring por ID."""
        ...


class IPromptCollector(ABC):
    """Puerto para recolección de prompts/contexto.

    El engine NO conoce features.main.prompts. Solo necesita recolectar
    prompts. El main implementa este puerto.
    """

    @abstractmethod
    def collect(self, chain_dir: str, context: dict[str, Any]) -> dict[str, Any]:
        """Recolecta prompts para el contexto."""
        ...


class ITracer(ABC):
    """Puerto para trazabilidad de eventos del workflow (TraceGraph)."""

    @abstractmethod
    def trace(
        self,
        chain_dir: str,
        event_type: str,
        data: dict | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        ...

    @abstractmethod
    def trace_auto(
        self,
        chain_dir: str,
        sub_state: str,
        stdout: str,
        stderr: str,
        exit_code: int,
        task_id: str | None = None,
    ) -> None:
        ...

    @abstractmethod
    def trace_submit(
        self,
        chain_dir: str,
        sub_state: str,
        decision: str,
        evidence: str,
        task_id: str | None = None,
    ) -> None:
        ...

    @abstractmethod
    def trace_transition(
        self,
        chain_dir: str,
        gate: str,
        from_node: str,
        to_node: str,
        task_id: str | None = None,
    ) -> None:
        ...

    @abstractmethod
    def trace_error(
        self,
        chain_dir: str,
        error_type: str,
        message: str,
        task_id: str | None = None,
    ) -> None:
        ...

    @abstractmethod
    def has_kg_query(
        self,
        chain_dir: str,
        task_id: str = "",
        session_id: str = "",
        layers: set[str] | list[str] | None = None,
    ) -> bool:
        ...

    @abstractmethod
    def replay(self, chain_dir: str, max_events: int | None = None) -> list[dict]:
        ...


__all__ = [
    "IWorkflowIO",
    "IWorkflowLoader",
    "IPresenter",
    "ITracer",
]
