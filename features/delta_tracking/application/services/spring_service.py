"""application/services/spring_service.py — SpringService (thin orchestrator).

Capa de aplicacion en Clean Architecture. Orquesta los casos de uso
delegando en el DeltaRepository. No contiene logica de negocio — solo
inyeccion de dependencias y delegacion.

Reemplaza al antiguo SpringGraphService (750+ lineas, God class).
"""
from __future__ import annotations

import os

from features.delta_tracking.application.ports.delta_repository import DeltaRepository
from features.delta_tracking.infrastructure.cognitive_delta_repo import CognitiveDeltaRepository
from features.delta_tracking.application.use_cases.create_spring import CreateSpringUseCase
from features.delta_tracking.application.use_cases.query_delta import QueryDeltaUseCase
from features.delta_tracking.application.use_cases.manage_specs import (
    AddSpecUseCase,
    UpdateSpecUseCase,
    UpsertSpecsUseCase,
)
from features.delta_tracking.application.use_cases.import_legacy import (
    ImportLegacySprintUseCase,
)
from features.delta_tracking.domain.entities import Spring, Spec
from features.delta_tracking.domain.values import EDGE_CONTAINS, EDGE_IMPACTS


def _spec_to_dict(spec: Spec) -> dict:
    d = spec.to_dict()
    d["_id"] = spec.doc_id
    return d


def _spring_to_dict(spring: Spring) -> dict:
    d = spring.to_dict()
    d["_id"] = spring.spring_id
    return d


class SpringService:
    """Orquestador delgado: delega en use cases via DeltaRepository."""

    def __init__(self, project_root: str = "", repo: DeltaRepository | None = None):
        self._repo = repo or CognitiveDeltaRepository(project_root)
        self._create = CreateSpringUseCase(self._repo)
        self._query = QueryDeltaUseCase(self._repo)
        self._add_spec = AddSpecUseCase(self._repo)
        self._update_spec = UpdateSpecUseCase(self._repo)
        self._upsert = UpsertSpecsUseCase(self._repo)
        self._import_legacy = ImportLegacySprintUseCase(self._repo)

    @classmethod
    def from_chain_dir(cls, chain_dir: str) -> "SpringService | None":
        if not chain_dir:
            return None
        try:
            from features.project import ProjectService
            paths = ProjectService.resolve_paths(chain_dir)
            data_dir = getattr(paths, "data_dir", "") or ""
            project_root = os.path.dirname(data_dir) if data_dir else ""
            if project_root and os.path.isdir(project_root):
                return cls(project_root=project_root)
        except Exception:
            pass
        return None

    # ── Spring lifecycle ──

    def create_spring(self, spring_id: str, goal: str = "", status: str = "planning",
                      project_id: str = "", session_id: str = "") -> dict:
        spring = self._create.execute(spring_id, goal, status, project_id, session_id)
        return _spring_to_dict(spring)

    def get_spring(self, spring_id: str) -> dict | None:
        s = self._query.get_spring(spring_id)
        return _spring_to_dict(s) if s else None

    def get_active_spring(self) -> dict | None:
        s = self._query.get_active_spring()
        return _spring_to_dict(s) if s else None

    def list_springs(self, status: str = "") -> list[dict]:
        return [_spring_to_dict(s) for s in self._query.list_springs(status)]

    def get_spring_id(self, chain_dir: str) -> str:
        return os.path.basename(os.path.normpath(chain_dir))

    def set_spring_status(self, spring_id: str, status: str) -> None:
        spring = self._repo.get_spring(spring_id)
        if spring:
            spring.status = status
            spring.active = (status == "active")
            self._repo.save_spring(spring)

    def set_spring_context(self, spring_id: str, context: dict) -> None:
        spring = self._repo.get_spring(spring_id)
        if spring:
            spring.context = context
            self._repo.save_spring(spring)

    def get_spring_context(self, spring_id: str) -> dict | None:
        spring = self._repo.get_spring(spring_id)
        return spring.context if spring else None

    def ensure_spring_imported(self, chain_dir: str, spring_id: str) -> dict | None:
        spring = self._repo.get_spring(spring_id)
        if spring:
            return _spring_to_dict(spring)
        try:
            result = self._import_legacy.execute(chain_dir)
            if result.get("imported"):
                s = self._repo.get_spring(spring_id)
                return _spring_to_dict(s) if s else None
        except Exception:
            pass
        return None

    def update_spring(self, spring_id: str, data: dict) -> None:
        """Actualiza campos de un spring existente sin crear uno nuevo."""
        spring = self._repo.get_spring(spring_id)
        if spring:
            for k, v in data.items():
                if hasattr(spring, k) and v is not None:
                    setattr(spring, k, v)
            self._repo.save_spring(spring)

    # ── Specs ──

    def add_spec(self, spring_id: str, objective: str, domain: str = "",
                 spec_type: str = "feature", spec_index: int | None = None,
                 result_state: str = "",
                 files: list[str] | None = None, status: str = "draft",
                 description: str = "") -> dict:
        spec = self._add_spec.execute(
            spring_id, objective, domain, spec_type, spec_index,
            result_state, files, status, description)
        return _spec_to_dict(spec)

    def update_spec(self, spring_id: str, spec_id: str, updates: dict) -> dict | None:
        s = self._update_spec.execute(spring_id, spec_id, updates)
        return _spec_to_dict(s) if s else None

    def update_spec_status(self, spring_id: str, spec_id: str, status: str) -> dict | None:
        s = self._update_spec.execute(spring_id, spec_id, {"status": status})
        return _spec_to_dict(s) if s else None

    def upsert_spec(self, spring_id: str, objective: str, domain: str = "",
                    spec_type: str = "feature", result_state: str = "",
                    files: list[str] | None = None, status: str = "draft",
                    description: str = "", **extra) -> dict:
        """Upsert unico (delega en batch con 1 item)."""
        return self._upsert.execute(spring_id, [{
            "objective": objective,
            "domain": domain,
            "type": spec_type,
            "result_state": result_state,
            "files": files or [],
            "status": status,
            "description": description,
            **extra,
        }])

    def upsert_specs_batch(self, spring_id: str, items: list[dict]) -> dict:
        return self._upsert.execute(spring_id, items)

    def get_specs(self, spring_id: str, status: str = "") -> list[dict]:
        return [_spec_to_dict(s) for s in self._query.get_specs(spring_id, status)]

    def get_specs_legacy(self, spring_id: str) -> list[dict]:
        """Proyeccion legacy: specs como dicts (impacted_artifacts.files)."""
        specs = self._query.get_specs(spring_id)
        return [
            {
                "task_id": s.spec_id,
                "objective": s.objective,
                "domain": s.domain,
                "type": s.spec_type,
                "status": s.status,
                "result_state": s.result_state,
                "description": s.description,
                "impacted_artifacts": {"files": s.files},
                "predicates": s.predicates or {},
                "depends_on": s.depends_on or [],
            }
            for s in specs
        ]

    def get_spec(self, spring_id: str, spec_id: str) -> dict | None:
        s = self._query.get_spec(spring_id, spec_id)
        return _spec_to_dict(s) if s else None

    def get_current_spec(self, spring_id: str) -> dict | None:
        s = self._query.get_current_spec(spring_id)
        return _spec_to_dict(s) if s else None

    # ── Import legacy ──

    def import_spring(self, sprint_dir: str) -> dict:
        return self._import_legacy.execute(sprint_dir)

    # ── Proyeccion legacy ──

    def sync_plan_projection(self, spring_id: str) -> None:
        """Regenera plan.json (proyeccion de specs) para consumidores legacy."""
        from features.spring.infrastructure.repositories import PlanRepository

        spring = self._repo.get_spring(spring_id)
        specs = self._repo.get_specs(spring_id)

        legacy_specs = []
        for spec in specs:
            legacy_specs.append({
                "task_id": spec.spec_id,
                "objective": spec.objective,
                "domain": spec.domain,
                "type": spec.spec_type,
                "status": spec.status,
                "result_state": spec.result_state,
                "task_description": spec.description,
                "impacted_artifacts": {"files": spec.files},
                "predicates": spec.predicates or {},
                "depends_on": spec.depends_on or [],
            })

        plan = {
            "version": "2.0",
            "chain_id": spring_id,
            "goal": spring.goal if spring else "",
            "specifications": legacy_specs,
        }
        import json
        sprint_dir = os.path.join(
            os.path.dirname(os.path.dirname(spring_id)),
            "delta", spring_id,
        ) if spring else ""
        if sprint_dir and os.path.isdir(sprint_dir):
            plan_path = os.path.join(sprint_dir, "plan.json")
            with open(plan_path, "w") as f:
                json.dump(plan, f, indent=2)
