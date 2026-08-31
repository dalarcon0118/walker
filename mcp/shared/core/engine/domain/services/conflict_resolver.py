"""domain/services/conflict_resolver.py — Resolución de conflictos del workflow.

P1: Complementa BlockResolver con estrategias específicas de dominio:
- skip/force: delegan en BlockResolver (engine)
- edit/merge: delegan en ConflictResolver de delta_tracking (specs)

SRP: esta clase opera sobre WorkflowState y WorkflowGraph (engine).
Las operaciones sobre specs (edit/merge) se delegan a delta_tracking.
"""

from __future__ import annotations

from typing import Any

from returns.result import Result

from shared.core.engine.domain.entities import WorkflowState, WorkflowGraph
from shared.core.engine.domain.services.block_resolver import BlockResolver


class WorkflowConflictResolver:
    """Resuelve conflictos del workflow aplicando estrategias.

    Estrategias:
    - skip: marca sub-estado como completado sin datos (BlockResolver)
    - force: permite submit con advertencia (BlockResolver)
    - escalate: transiciona a nodo de escalación (BlockResolver)
    - compensate: ejecuta compensación (BlockResolver)
    - edit: actualiza spec en delta_tracking (delegado)
    - merge: integra specs sin perder información (delegado)

    NOTA (ADR-WALKER-067): las estrategias edit/merge operan sobre specs del
    delta, que es INCUMBENCIA DEL HOST. El engine NO conoce IConflictResolver;
    solo implementa la mecánica de resolución (skip/force/escalate/compensate
    vía BlockResolver). El host aplica las estrategias de specs externamente.
    """

    def resolve(
        self,
        state: WorkflowState,
        strategy: str,
        payload: dict[str, Any],
        graph: WorkflowGraph | None = None,
    ) -> Result[WorkflowState, str]:
        """Resuelve un conflicto aplicando la estrategia indicada.

        Args:
            state: Estado actual del workflow
            strategy: Estrategia (skip | force | escalate | compensate)
            payload: Datos adicionales para la resolución
            graph: Grafo de workflow (necesario para escalate)

        Returns:
            Success(new_state) si la resolución fue exitosa
            Failure(reason) si no se pudo resolver
        """
        return BlockResolver.resolve(state, strategy, payload, graph=graph)


from returns.result import Success, Failure
