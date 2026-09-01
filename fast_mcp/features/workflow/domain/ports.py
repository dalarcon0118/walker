"""domain/ports.py — Ports (interfaces) of the workflow domain.

Only 8 ports needed for Phase 3 (submit/transition/status).
The remaining 8 are deferred to Phase 3.1.

PRINCIPLE: the domain defines WHAT it needs, not HOW it's implemented.
Infrastructure adapters implement these ports.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class IWorkflowStore(ABC):
    """Unified port for workflow access (graph + state).

    Replaces workflow_repo + state_repo. The engine consumes ONLY these
    3 methods; the port does NOT expose concrete repos.
    """

    @abstractmethod
    def load_graph(self) -> Any:
        """Load the workflow graph/config."""
        ...

    @abstractmethod
    def load_state(self, chain_dir: str) -> Any:
        """Load the current workflow state (default if not exists)."""
        ...

    @abstractmethod
    def save_state(self, chain_dir: str, state: Any) -> Any:
        """Persist the workflow state. Returns the saved state with incremented revision."""
        ...


class ITracer(ABC):
    """Port for workflow event tracing (TraceGraph)."""

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
    def trace_event(self, event: Any) -> None:
        """Register a typed event (WorkflowEvent)."""
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


class ISprintService(ABC):
    """Port for sprint/spring access.

    delta_tracking implements this port. Stub returns empty data.
    """

    @abstractmethod
    def load_tasks(self, chain_dir: str) -> list[dict]:
        """Load tasks from the sprint."""
        ...

    @abstractmethod
    def get_spring(self, spring_id: str) -> dict | None:
        """Get a spring by ID."""
        ...


class IConflictResolver(ABC):
    """Port for workflow conflict resolution.

    delta_tracking implements this port. Stub returns no conflict.
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
        """Resolve a conflict using the given strategy."""
        ...


class IWorkflowIO(ABC):
    """Port for workflow IO operations.

    Abstracs: script execution, stderr output, sub-state access.
    """

    @abstractmethod
    def run_script(self, script: str, chain_dir: str | None = None) -> dict[str, Any]:
        """Execute a shell script and return result."""
        ...

    @abstractmethod
    def eprint(self, *args: Any, **kwargs: Any) -> None:
        """Print to stderr."""
        ...


class ISideEffectEngine(ABC):
    """Port for side-effect execution (BPMN compensation)."""

    @abstractmethod
    def run_compensation_handler(self, handler_name: str, ctx: Any) -> Any:
        """Execute a compensation handler by name."""
        ...


class IProjectContext(ABC):
    """Port for project context access (paths, metadata).

    The engine does NOT know about features.project. It only needs
    to resolve project paths. project/ implements this port.
    """

    @abstractmethod
    def resolve_paths(self, chain_dir: str) -> Any:
        """Resolve project paths from chain_dir."""
        ...

    @abstractmethod
    def list_available(self) -> list[dict]:
        """List available projects."""
        ...


class ICpgAnalysisPort(ABC):
    """Port for CPG analysis operations.

    code_analysis implements this port.
    """

    @abstractmethod
    def business_layer(self, focus: str = "") -> dict[str, Any]:
        """Return the business layer of the CPG."""
        ...

    @abstractmethod
    def detect_unused_functions(self, file_filter: str = "") -> dict[str, Any]:
        """Detect unused functions/methods."""
        ...

    @abstractmethod
    def review_code(self, files: list[str] | None = None) -> dict[str, Any]:
        """Run code review."""
        ...

    @abstractmethod
    def project_metrics(self, file_filter: str = "") -> dict[str, Any]:
        """Compute project metrics."""
        ...


__all__ = [
    "IWorkflowStore",
    "ITracer",
    "ISprintService",
    "IConflictResolver",
    "IWorkflowIO",
    "ISideEffectEngine",
    "IProjectContext",
    "ICpgAnalysisPort",
]
