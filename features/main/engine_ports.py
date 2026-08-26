"""engine_ports.py — Implementaciones de puertos del engine.

El engine define interfaces (puertos) en shared/core/engine/domain/ports.py.
Este módulo implementa esos puertos delegando a los services de features
específicos. El composition root (bootstrap.py) inyecta estas implementaciones
en el engine.

Esto elimina el acoplamiento del engine a features individuales.
"""
from __future__ import annotations

from typing import Any

from shared.core.engine.domain.ports import (
    IProjectContext,
    IConflictResolver,
    ISprintService,
    IPromptCollector,
)


class ProjectContextAdapter(IProjectContext):
    """Implementa IProjectContext delegando a ProjectService."""

    def __init__(self, project_service: Any):
        self._project_service = project_service

    def resolve_paths(self, chain_dir: str) -> Any:
        return self._project_service.resolve_paths(chain_dir)

    def get_workflow_json_path(self, chain_dir: str) -> str:
        paths = self._project_service.resolve_paths(chain_dir)
        return getattr(paths, "workflow_json_path", "")

    def list_available(self) -> list[dict]:
        return self._project_service.list_available()

    def find_active_sprint(self, project_id: str) -> str:
        return self._project_service.find_active_sprint(project_id)


class ConflictResolverAdapter(IConflictResolver):
    """Implementa IConflictResolver delegando a ConflictResolver de delta_tracking."""

    def __init__(self, conflict_resolver: Any):
        self._conflict_resolver = conflict_resolver

    def resolve(
        self,
        conflict: Any,
        strategy: str,
        payload: dict[str, Any],
        workflow_state: dict[str, Any],
        specs: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return self._conflict_resolver.resolve(
            conflict=conflict,
            strategy=strategy,
            payload=payload,
            workflow_state=workflow_state,
            specs=specs,
        )


class SprintServiceAdapter(ISprintService):
    """Implementa ISprintService delegando a SpringService de delta_tracking."""

    def __init__(self, spring_service_factory: Any):
        self._spring_service_factory = spring_service_factory

    def from_chain_dir(self, chain_dir: str) -> Any:
        return self._spring_service_factory(chain_dir=chain_dir)

    def load_tasks(self, chain_dir: str) -> list[dict]:
        svc = self.from_chain_dir(chain_dir)
        if not svc:
            return []
        return svc.load_tasks(chain_dir)

    def get_spring(self, spring_id: str) -> dict | None:
        # SpringService.from_chain_dir necesita chain_dir, no spring_id
        # Para obtener un spring por ID necesitamos el project_root
        return None


class PromptCollectorAdapter(IPromptCollector):
    """Implementa IPromptCollector delegando al collector de features/main."""

    def __init__(self, collector: Any):
        self._collector = collector

    def collect(self, chain_dir: str, context: dict[str, Any]) -> dict[str, Any]:
        return self._collector(chain_dir, context)
