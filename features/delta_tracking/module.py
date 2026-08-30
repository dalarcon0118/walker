"""features/delta_tracking/module.py — Manifiesto del bounded context `delta_tracking` (ADR-066 D4).

Adaptadores de los puertos `IConflictResolver` e `ISprintService` del engine.
"""
from __future__ import annotations

from typing import Any

from shared.core.engine.domain.ports import IConflictResolver, ISprintService
from features.delta_tracking import SpringService
from features.delta_tracking.domain.services.conflict_resolver import ConflictResolver
from shared.libs.module_system.contracts import ModuleManifest


class ConflictResolverAdapter(IConflictResolver):
    """Implementa IConflictResolver delegando a ConflictResolver de delta_tracking."""

    def __init__(self, delta_conflict_resolver: Any):
        self._conflict_resolver = delta_conflict_resolver

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

    def __init__(self, sprint_service: Any):
        self._spring_service_factory = sprint_service

    def from_chain_dir(self, chain_dir: str) -> Any:
        return self._spring_service_factory(chain_dir=chain_dir)

    def load_tasks(self, chain_dir: str) -> list[dict]:
        svc = self.from_chain_dir(chain_dir)
        if not svc:
            return []
        return svc.load_tasks(chain_dir)

    def get_spring(self, spring_id: str) -> dict | None:
        # SpringService.from_chain_dir necesita chain_dir, no spring_id
        return None


MODULE = ModuleManifest(
    name="delta_tracking",
    imports=["shared"],
    adapters=[
        (IConflictResolver, ConflictResolverAdapter),
        (ISprintService, SprintServiceAdapter),
    ],
    exports=["conflict_resolver", "sprint_service"],
)

# Auto-registro por import (ADR-WALKER-068): el composition root importa este
# modulo; al importarlo se registra su manifest. Sin lista central de paths.
from shared.libs.module_system.global_registry import register_manifest as _register_manifest

_register_manifest(MODULE)
