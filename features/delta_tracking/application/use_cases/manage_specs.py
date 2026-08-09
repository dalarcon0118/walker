"""application/use_cases/manage_specs.py — Spec management use cases.

SRP: agregar, actualizar, upsert specs en un spring del DeltaGraph.
"""
from __future__ import annotations

from features.delta_tracking.domain.entities import Spec
from features.delta_tracking.domain.services.spec_service import SpecService
from features.delta_tracking.domain.values import EDGE_CONTAINS, EDGE_IMPACTS, EDGE_DEPENDS
from features.delta_tracking.application.ports.delta_repository import DeltaRepository


class AddSpecUseCase:
    """Agrega una spec a un spring."""

    def __init__(self, repo: DeltaRepository):
        self._repo = repo

    def execute(
        self,
        spring_id: str,
        objective: str,
        domain: str = "",
        spec_type: str = "feature",
        spec_index: int | None = None,
        result_state: str = "",
        files: list[str] | None = None,
        status: str = "draft",
        description: str = "",
    ) -> Spec:
        existing = self._repo.get_specs(spring_id)
        spec_index = spec_index if spec_index is not None else len(existing)
        spec = Spec(
            spring_id=spring_id,
            objective=objective,
            domain=domain,
            spec_type=spec_type,
            spec_index=spec_index,
            result_state=result_state,
            files=files or [],
            status=status,
            description=description,
        )
        self._repo.save_spec(spec)
        self._repo.add_edge(spring_id, spec.doc_id, EDGE_CONTAINS)
        for fpath in spec.files:
            self._repo.add_edge(spec.doc_id, fpath, EDGE_IMPACTS)
        for dep in spec.depends_on:
            self._repo.add_edge(spec.doc_id, dep, EDGE_DEPENDS)
        return spec


class UpdateSpecUseCase:
    """Actualiza campos de una spec existente."""

    def __init__(self, repo: DeltaRepository):
        self._repo = repo

    def execute(self, spring_id: str, spec_id: str, updates: dict) -> Spec | None:
        spec = self._repo.get_spec(spring_id, spec_id)
        if spec is None:
            return None
        updated = SpecService.merge(spec, updates)
        self._repo.save_spec(updated)
        return updated


class UpsertSpecsUseCase:
    """Upsert batch: create or update por objective en 1 store_many."""

    def __init__(self, repo: DeltaRepository):
        self._repo = repo

    def execute(self, spring_id: str, items: list[dict]) -> dict:
        existing_specs = self._repo.get_specs(spring_id)
        obj_to_spec = {s.objective.strip(): s for s in existing_specs if s.objective.strip()}

        batch: list[Spec] = []
        created = 0
        refined = 0

        for item in items:
            objective = item.get("objective", "").strip()
            if not objective:
                continue
            existing = obj_to_spec.get(objective)

            if existing is not None:
                merged = SpecService.merge(existing, item)
                batch.append(merged)
                refined += 1
            else:
                spec_index = len(existing_specs) + created
                spec = Spec(
                    spring_id=spring_id,
                    objective=objective,
                    domain=item.get("domain", ""),
                    spec_type=item.get("type", "feature"),
                    spec_index=spec_index,
                    result_state=item.get("result_state", ""),
                    files=item.get("files", []),
                    status=item.get("status", "draft"),
                    description=item.get("description", item.get("task_description", "")),
                )
                batch.append(spec)
                obj_to_spec[objective] = spec
                created += 1

        if batch:
            self._repo.save_specs_batch(batch)
            for spec in batch:
                self._repo.add_edge(spring_id, spec.doc_id, EDGE_CONTAINS)

        return {"ok": True, "created": created, "refined": refined, "total": len(batch)}
