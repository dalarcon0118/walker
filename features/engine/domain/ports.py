"""domain/ports.py — Puertos (interfaces) del dominio Workflow Navigation.

PRINCIPIO: El dominio define QUÉ necesita, no CÓMO se implementa.
Los adaptadores de infrastructure implementan estos puertos.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from features.engine.domain.entities import WorkflowGraph, WorkflowState


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


class IWorkflowLoader(ABC):
    """Puerto para carga de workflow y estado desde disco."""

    @abstractmethod
    def load_workflow(self, chain_dir: str) -> WorkflowGraph:
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
