"""application/services/spring_service.py — SpringService (thin orchestrator).

Capa de aplicacion en Clean Architecture. Orquesta los casos de uso
delegando en el DeltaRepository. No contiene logica de negocio — solo
inyeccion de dependencias y delegacion.

Responsabilidades separadas (SRP):
  - Presentacion (display_name): application/presenters.py
  - Proyecciones legacy (Spec → task dict): application/projections.py
  - Mutaciones del nodo spring: use_cases/update_spring.py
  - Deriva del spring_id: domain/values.py (spring_id_from_chain_dir)

Reemplaza al antiguo SpringGraphService (750+ lineas, God class).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from returns.pipeline import is_successful
from returns.result import safe

from features.delta_tracking.application.ports.delta_repository import DeltaRepository
from features.delta_tracking.infrastructure.cognitive_delta_repo import (
    CognitiveDeltaRepository,
)
from features.delta_tracking.application.use_cases.create_spring import (
    CreateSpringUseCase,
)
from features.delta_tracking.application.use_cases.query_delta import QueryDeltaUseCase
from features.delta_tracking.application.use_cases.update_spring import (
    UpdateSpringUseCase,
)
from features.delta_tracking.application.use_cases.manage_specs import (
    AddSpecUseCase,
    UpdateSpecUseCase,
    UpsertSpecsUseCase,
)
from features.delta_tracking.application.use_cases.progress import (
    GetSprintProgressUseCase,
    GetTaskContextUseCase,
    ListCompletedTasksUseCase,
)
from features.delta_tracking.application.presenters import (
    derive_display_name,
    with_display_name,
)
from features.delta_tracking.application.projections import (
    load_spring_tasks,
    specs_as_tasks,
)
from features.delta_tracking.domain.entities import Spring, Spec
from features.delta_tracking.domain.values import spring_id_from_chain_dir

logger = logging.getLogger(__name__)


def _spec_to_dict(spec: Spec) -> dict:
    d = spec.to_dict()
    d["_id"] = spec.doc_id
    return d


def _spring_to_dict(spring: Spring) -> dict:
    d = spring.to_dict()
    d["_id"] = spring.spring_id
    return d


@safe
def _ensure_spring_core(repo: Any, spring_id: str) -> dict | None:
    """Core puro: devuelve el spring si existe en el delta (puede lanzar)."""
    spring = repo.get_spring(spring_id)
    return _spring_to_dict(spring) if spring else None


class SpringService:
    """Orquestador delgado: delega en use cases via DeltaRepository."""

    def __init__(self, project_root: str = "", repo: DeltaRepository | None = None, graph_factory: Callable[[str], Any] | None = None):
        self.project_root = os.path.normpath(project_root) if project_root else ""
        self._repo = repo or CognitiveDeltaRepository(project_root, graph_factory=graph_factory)
        self._create = CreateSpringUseCase(self._repo)
        self._query = QueryDeltaUseCase(self._repo)
        self._update_spring = UpdateSpringUseCase(self._repo)
        self._add_spec = AddSpecUseCase(self._repo)
        self._update_spec = UpdateSpecUseCase(self._repo)
        self._upsert = UpsertSpecsUseCase(self._repo)
        self._progress = GetSprintProgressUseCase(self._repo)
        self._task_ctx = GetTaskContextUseCase(self._repo)
        self._completed = ListCompletedTasksUseCase(self._repo)

    @classmethod
    def from_chain_dir(cls, chain_dir: str, graph_factory: Callable[[str], Any] | None = None) -> "SpringService | None":
        """Construye un SpringService desde el chain_dir del sprint.

        Result pattern (convention-001): el core @safe nunca lanza. La
        ausencia de proyecto registrado es el caso esperable (retorna None
        en silencio); los fallos reales de resolucion se loggean en warning.
        """
        if not chain_dir:
            return None
        result = _resolve_project_root_core(chain_dir, cls, graph_factory=graph_factory)
        if is_successful(result):
            return result.value_or(None)
        error = result.failure()
        logger.warning(
            "resolve/init fallo para %s (%s: %s); sin SpringService",
            chain_dir, type(error).__name__, error,
        )
        return None

    # ── Spring lifecycle ──

    def create_spring(
        self,
        spring_id: str,
        goal: str = "",
        status: str = "planning",
        project_id: str = "",
        session_id: str = "",
        **extra,
    ) -> dict:
        if not extra.get("display_name"):
            extra["display_name"] = derive_display_name(goal, spring_id)
        spring = self._create.execute(
            spring_id, goal, status, project_id, session_id, **extra
        )
        return _spring_to_dict(spring)

    def get_spring(self, spring_id: str) -> dict | None:
        s = self._query.get_spring(spring_id)
        return with_display_name(_spring_to_dict(s) if s else None)

    def get_active_spring(self) -> dict | None:
        s = self._query.get_active_spring()
        return with_display_name(_spring_to_dict(s) if s else None)

    def list_springs(self, status: str = "") -> list[dict]:
        return [
            with_display_name(_spring_to_dict(s))
            for s in self._query.list_springs(status)
        ]

    # ── Entity-returning variants (P2 cleanup) ───────────────────────

    def create_spring_entity(self, spring_id: str, goal: str = "", **kwargs) -> Spring:
        return self._create.execute(spring_id, goal, **kwargs)

    def get_spring_entity(self, spring_id: str) -> Spring | None:
        return self._query.get_spring(spring_id)

    def get_active_spring_entity(self) -> Spring | None:
        return self._query.get_active_spring()

    def list_springs_entity(self, status: str = "") -> list[Spring]:
        return self._query.list_springs(status)

    def get_spring_id(self, chain_dir: str) -> str:
        return spring_id_from_chain_dir(chain_dir)

    def set_spring_status(self, spring_id: str, status: str) -> None:
        self._update_spring.set_status(spring_id, status)

    def set_spring_context(self, spring_id: str, context: dict | None = None, **extra) -> None:
        self._update_spring.set_context(spring_id, context, **extra)

    def get_spring_context(self, spring_id: str) -> dict | None:
        spring = self._query.get_spring(spring_id)
        return spring.context if spring else None

    def ensure_spring_imported(self, chain_dir: str, spring_id: str) -> dict | None:
        """Garantiza que el spring exista en el DeltaGraph (fuente unica).

        No existe importacion legacy: el spring se crea via create_spring en
        el delta. Si no esta registrado, se degrada a None.

        Result pattern (convention-001): el core @safe nunca lanza; ante
        fallo se loggea warning y se degrade a None — nunca silencioso.
        """
        result = _ensure_spring_core(self._repo, spring_id)
        if is_successful(result):
            return result.value_or(None)
        error = result.failure()
        logger.warning(
            "ensure_spring_imported fallo para %s (%s: %s); spring no disponible",
            chain_dir, type(error).__name__, error,
        )
        return None

    def update_spring(self, spring_id: str, data: dict) -> None:
        """Actualiza campos de un spring existente sin crear uno nuevo."""
        self._update_spring.execute(spring_id, data)

    # ── Specs ──

    def add_spec(
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
    ) -> dict:
        spec = self._add_spec.execute(
            spring_id,
            objective,
            domain,
            spec_type,
            spec_index,
            result_state,
            files,
            status,
            description,
        )
        return _spec_to_dict(spec)

    def update_spec(self, spring_id: str, spec_id: str, updates: dict) -> dict | None:
        s = self._update_spec.execute(spring_id, spec_id, updates)
        return _spec_to_dict(s) if s else None

    def update_spec_status(
        self,
        spring_id: str,
        spec_id: str,
        status: str,
    ) -> dict | None:
        s = self._update_spec.execute(spring_id, spec_id, {"status": status})
        return _spec_to_dict(s) if s else None

    def upsert_spec(
        self,
        spring_id: str,
        objective: str,
        domain: str = "",
        spec_type: str = "feature",
        result_state: str = "",
        files: list[str] | None = None,
        status: str = "draft",
        description: str = "",
        **extra,
    ) -> dict:
        """Upsert unico (delega en batch con 1 item)."""
        result = self._upsert.execute(
            spring_id,
            [
                {
                    "objective": objective,
                    "domain": domain,
                    "type": spec_type,
                    "result_state": result_state,
                    "files": files or [],
                    "status": status,
                    "description": description,
                    **extra,
                }
            ],
        )
        if isinstance(result, dict) and result.get("ok"):
            return result
        return {"ok": False, "error": str(result)}

    def upsert_specs_batch(self, spring_id: str, items: list[dict]) -> dict:
        result = self._upsert.execute(spring_id, items)
        if isinstance(result, dict) and result.get("ok"):
            return result
        return {"ok": False, "error": str(result)}

    def get_specs(self, spring_id: str, status: str = "") -> list[dict]:
        return [_spec_to_dict(s) for s in self._query.get_specs(spring_id, status)]

    def get_specs_legacy(self, spring_id: str) -> list[dict]:
        """Proyeccion legacy: specs como dicts (impacted_artifacts.files).

        Delegada a application/projections.py (specs_as_tasks). Incluye
        spec_index (SSOT Task/Spec) para que los reindexadores
        (index_service) reconstruyan el spec_id/doc_id CANONICO y no
        re-numeren specs con gaps (idempotencia).
        """
        return specs_as_tasks(self._query.get_specs(spring_id))

    def get_spec(self, spring_id: str, spec_id: str) -> dict | None:
        s = self._query.get_spec(spring_id, spec_id)
        return _spec_to_dict(s) if s else None

    def get_current_spec(self, spring_id: str) -> dict | None:
        s = self._query.get_current_spec(spring_id)
        return _spec_to_dict(s) if s else None

    # ── Entity-returning variants (P2 cleanup) ───────────────────────

    def add_spec_entity(self, spring_id: str, objective: str, **kwargs) -> Spec:
        return self._add_spec.execute(spring_id, objective, **kwargs)

    def update_spec_entity(self, spring_id: str, spec_id: str, updates: dict) -> Spec | None:
        return self._update_spec.execute(spring_id, spec_id, updates)

    def get_specs_entity(self, spring_id: str, status: str = "") -> list[Spec]:
        return self._query.get_specs(spring_id, status)

    def get_spec_entity(self, spring_id: str, spec_id: str) -> Spec | None:
        return self._query.get_spec(spring_id, spec_id)

    def get_current_spec_entity(self, spring_id: str) -> Spec | None:
        return self._query.get_current_spec(spring_id)

    # ── Progress & context ──

    def get_sprint_progress(self, spring_id: str) -> dict:
        return self._progress.execute(spring_id)

    def get_task_context(self, spring_id: str, spec_id: str) -> dict | None:
        return self._task_ctx.execute(spring_id, spec_id)

    def list_completed_tasks(self, spring_id: str) -> list[dict]:
        return self._completed.execute(spring_id)

    # ── Lectura unica para el engine (tasks desde chain_dir) ──

    def load_tasks(self, chain_dir: str) -> list[dict]:
        """Tasks del sprint desde el DELTA (fuente unica, ADR-WALKER-013).

        Orquestacion delegada a application/projections.py
        (load_spring_tasks), que resuelve el rail del chain_dir y devuelve
        las specs como tasks (task_id, objective, domain, type, status,
        result_state, impacted_artifacts.files, ...). No lee plan.json.
        """
        if not chain_dir:
            return []
        return load_spring_tasks(self, chain_dir)

    # ── Indexación en CognitiveGraph (ADR-WALKER-049) ────────────────

    def index_sprint(self, chain_dir: str, cg: Any) -> int:
        """Indexa las tareas del sprint en el indice delta de CognitiveGraph.

        Args:
            chain_dir: Directorio del sprint.
            cg: Instancia de CognitiveGraph.

        Returns:
            Numero de specs indexadas.
        """
        from features.delta_tracking.application.index_service import index_sprint

        return index_sprint(chain_dir, cg)

    def index_all(self, project_root: str, cg: Any) -> int:
        """Indexa todos los sprints del proyecto en el indice delta.

        Args:
            project_root: Directorio cognitivo del proyecto.
            cg: Instancia de CognitiveGraph.

        Returns:
            Numero total de specs indexadas.
        """
        from features.delta_tracking.application.index_service import index_all

        return index_all(project_root, cg)


# ── Cores @safe (Result pattern, convention-001) ─────────────────

@safe
def _resolve_project_root_core(chain_dir: str, factory, graph_factory: Callable[[str], Any] | None = None) -> "SpringService | None":
    """Core puro de SpringService.from_chain_dir (puede lanzar).

    Resuelve el project_root desde el chain_dir via ProjectService.resolve_paths
    y construye el servicio con el factory (cls). El limite @safe cubre TAMBIEN
    la construccion de CognitiveDeltaRepository (IO de CognitiveGraph): si falla,
    degrada observable — el wrapper decide el nivel de log.

    Devuelve None si no resuelve (caso esperable: sin proyecto registrado).
    """
    # NOTE: ProjectService import moved inside function to break cycle
    # features.delta_tracking -> features.project (detected by Tarjan gate).
    # Resolved dynamically at runtime via module service locator.
    from core.engine.bootstrap import resolve_service

    project_service_factory = resolve_service("project_service")
    if project_service_factory is None:
        return None
    paths = project_service_factory.resolve_paths(chain_dir)
    data_dir = getattr(paths, "data_dir", "") or ""
    project_root = os.path.dirname(data_dir) if data_dir else ""
    if project_root and os.path.isdir(project_root):
        return factory(project_root=project_root, graph_factory=graph_factory)
    return None
