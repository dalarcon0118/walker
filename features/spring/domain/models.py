"""
domain/models.py — Spring domain entities.

SprintState extiende WorkflowState (engine) agregando datos de negocio
del sprint: tasks, goal, progress, etc.

PRINCIPIO: El workflow engine no sabe de estos datos.
Spring es UN caso de uso del workflow, no el workflow mismo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from features.engine.domain.models import WorkflowState


@dataclass(frozen=True)
class SprintState(WorkflowState):
    """Estado del sprint extendiendo WorkflowState con datos de negocio.

    Agrega al estado puro del workflow los datos específicos del sprint:
    - sprint: metadatos del sprint (goal, project_id, etc.)
    - tasks: lista de tareas del backlog
    - task_order: orden de ejecucion de tareas
    - walker: datos del agente walker (decision_log, contract_data, etc.)
    - audit: trail de auditoria
    - state: estado global del proyecto
    - progress: progreso del sprint
    """

    # Datos de sprint (bridge con SpringContext)
    sprint: dict[str, Any] = field(default_factory=dict)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    task_order: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    walker: dict[str, Any] = field(default_factory=dict)
    audit: dict[str, Any] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serializa todos los campos (workflow + spring)."""
        base = super().to_dict()
        base.update({
            "sprint": dict(self.sprint),
            "tasks": list(self.tasks),
            "task_order": list(self.task_order),
            "state": dict(self.state),
            "walker": dict(self.walker),
            "audit": dict(self.audit),
            "progress": dict(self.progress),
        })
        return base

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SprintState:
        """Reconstruye desde dict (todos los campos)."""
        base = WorkflowState.from_dict(data)
        return cls(
            # WorkflowState fields
            version=base.version,
            current_node=base.current_node,
            current_sub_state=base.current_sub_state,
            sub_state_index=base.sub_state_index,
            sub_state_data=base.sub_state_data,
            node_executions=base.node_executions,
            history=base.history,
            context=base.context,
            updated_at=base.updated_at,
            # SprintState fields
            sprint=dict(data.get("sprint", {})),
            tasks=list(data.get("tasks", data.get("specifications", []))),
            task_order=list(data.get("task_order", [])),
            state=dict(data.get("state", {})),
            walker=dict(data.get("walker", {})),
            audit=dict(data.get("audit", {})),
            progress=dict(data.get("progress", {})),
        )
