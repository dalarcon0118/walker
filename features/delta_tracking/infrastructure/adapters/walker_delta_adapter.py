"""delta_tracking/infrastructure/adapters/walker_delta_adapter.py — Adapter hacia task_execution.

Implementa IDeltaService, ISprintService, ISpecService (puertos definidos
en domain/services/) orquestando CognitiveDeltaRepository + entidades
de dominio Delta.

SRP: este archivo es el ÚNICO punto de acoplamiento entre delta_tracking
y task_execution. Si task_execution cambia el puerto, solo este adapter
se actualiza.
"""
from __future__ import annotations

from features.delta_tracking.application.ports.delta_repository import DeltaRepository
from features.delta_tracking.domain.entities import Spring, Spec
from features.delta_tracking.domain.services.delta_service import IDeltaService, ISprintService, ISpecService
from features.delta_tracking.domain.services.spec_service import SpecService
from features.delta_tracking.domain.values import TYPE_SPRING, TYPE_SPEC, spec_id_from_index, spec_doc_id


class WalkerDeltaAdapter(IDeltaService, ISprintService, ISpecService):
    """Implementación de los puertos de task_execution sobre delta_tracking."""

    def __init__(self, repo: DeltaRepository, event_bus=None):
        self._repo = repo
        self._bus = event_bus

    # ── IDeltaService ──

    def create_spring(self, goal: str, project_id: str = "", **kwargs) -> dict[str, object]:
        spring_id = kwargs.get("spring_id", f"spring-{id(self):x}")
        spring = Spring(
            spring_id=spring_id,
            goal=goal,
            project_id=project_id,
            status="planning",
            active=False,
            **{k: v for k, v in kwargs.items() if k != "spring_id"}
        )
        self._repo.save_spring(spring)
        self._emit(SpringCreated(spring_id=spring_id, goal=goal, project_id=project_id))
        return spring.to_dict()

    def get_active_spring(self) -> dict[str, object] | None:
        spring = self._repo.get_active_spring()
        if spring is None:
            return None
        return spring.to_dict()

    def capture_task(self, task_data: dict) -> dict[str, object]:
        active = self._repo.get_active_spring()
        if active is None:
            return {"error": "No active spring"}
        specs = self._repo.get_specs(active.spring_id)
        spec = Spec(
            spring_id=active.spring_id,
            objective=task_data.get("objective", ""),
            domain=task_data.get("domain", ""),
            spec_type=task_data.get("spec_type", "feature"),
            spec_index=len(specs),
            files=task_data.get("files", []),
            status="draft",
        )
        self._repo.save_spec(spec)
        self._emit(SpecCreated(
            spec_id=spec.spec_id,
            spring_id=active.spring_id,
            objective=spec.objective,
        ))
        return spec.to_dict()

    def get_context(self, spring_id: str = "") -> dict[str, object]:
        if not spring_id:
            active = self._repo.get_active_spring()
            spring_id = active.spring_id if active else ""
        if not spring_id:
            return {"spring": None, "specs": []}
        spring = self._repo.get_spring(spring_id)
        specs = self._repo.get_specs(spring_id)
        return {
            "spring": spring.to_dict() if spring else None,
            "specs": [s.to_dict() for s in specs],
        }

    # ── ISprintService ──

    def list_springs(self, status: str = "") -> list[dict[str, object]]:
        springs = self._repo.list_springs(status)
        return [s.to_dict() for s in springs]

    def update_spring(self, spring_id: str, changes: dict) -> dict[str, object]:
        spring = self._repo.get_spring(spring_id)
        if spring is None:
            return {"error": f"Spring {spring_id} not found"}
        updated = SpecService.merge(spring, changes) if hasattr(SpecService, "merge") else spring
        # Spring no tiene merge en entidad; delegar en use case
        from features.delta_tracking.application.use_cases.update_spring import UpdateSpringUseCase
        updated = UpdateSpringUseCase(self._repo).execute(spring_id, changes)
        self._repo.save_spring(updated)
        self._emit(SpringUpdated(spring_id=spring_id, changes=changes))
        return updated.to_dict()

    def activate_spring(self, spring_id: str) -> dict[str, object]:
        return self.update_spring(spring_id, {"status": "active", "active": True})

    def close_spring(self, spring_id: str) -> dict[str, object]:
        return self.update_spring(spring_id, {"status": "closed", "active": False})

    # ── ISpecService ──

    def create_spec(self, spring_id: str, objective: str, **kwargs) -> dict[str, object]:
        specs = self._repo.get_specs(spring_id)
        spec = Spec(
            spring_id=spring_id,
            objective=objective,
            spec_index=len(specs),
            status="draft",
            **kwargs,
        )
        self._repo.save_spec(spec)
        self._emit(SpecCreated(spec_id=spec.spec_id, spring_id=spring_id, objective=objective))
        return spec.to_dict()

    def get_specs(self, spring_id: str, status: str = "") -> list[dict[str, object]]:
        specs = self._repo.get_specs(spring_id, status)
        return [s.to_dict() for s in specs]

    def update_spec(self, spring_id: str, spec_id: str, changes: dict) -> dict[str, object]:
        spec = self._repo.get_spec(spring_id, spec_id)
        if spec is None:
            return {"error": f"Spec {spec_id} not found in {spring_id}"}
        updated = SpecService.merge(spec, changes)
        self._repo.save_spec(updated)
        self._emit(SpecUpdated(spec_id=spec_id, spring_id=spring_id, changes=changes))
        return updated.to_dict()

    def complete_spec(self, spring_id: str, spec_id: str, result_state: str = "") -> dict[str, object]:
        return self.update_spec(spring_id, spec_id, {"status": "completed", "result_state": result_state})

    # ── Helpers ──

    def _emit(self, event):
        if self._bus is not None:
            try:
                self._bus.publish(event)
            except Exception:
                pass
