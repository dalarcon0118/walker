"""application/index_service.py — Indexacion de sprints via Spec (modelo unico).

Escribe las specs de un sprint en el indice 'delta' con el MISMO esquema
que lee CognitiveDeltaRepository (Spec.to_dict / doc_id = spec_key). Un
solo modelo (Spec) — la entidad ODM Task fue eliminada (SSOT).

La fuente de datos es el DeltaGraph (SpringService.load_tasks) y el
resultado es idempotente: re-indexar sobreescribe los mismos doc_ids
({spring}:{spec_id}) sin duplicar ni crear esquemas paralelos.
"""

from __future__ import annotations

import logging
import os

from returns.pipeline import is_successful
from returns.result import safe

from features.delta_tracking.domain.entities import Spec
from features.delta_tracking.domain.values import (
    EDGE_CONTAINS,
    EDGE_IMPACTS,
    EDGE_DEPENDS,
)

logger = logging.getLogger(__name__)


def index_sprint(chain_dir: str, cg) -> int:
    """Indexa las tareas de un sprint en CognitiveGraph (esquema Spec).

    Lee las specs del DELTA (SpringService — fuente unica) y las persiste
    en el indice delta con Spec.to_dict() y doc_id = spec_key (el mismo
    contrato que save_spec/save_specs_batch del repositorio).

    PRINCIPIO estructural: las specs del spring viven en el DeltaGraph;
    sprint-state.json y plan.json fueron eliminados (ADR-WALKER-013/014).

    Args:
        chain_dir: Ruta al directorio del sprint
        cg: Instancia de CognitiveGraph

    Returns:
        Numero de tareas indexadas
    """
    # Asegurar que el indice delta existe con schema fulltext (ODM bind
    # eliminado: el schema lo registra CognitiveGraph.register_defaults).
    if "delta" not in cg.list_indexes():
        cg.register_defaults()

    tasks = _load_spring_tasks(chain_dir)
    if not tasks:
        return 0

    sprint_id = os.path.basename(os.path.normpath(chain_dir))
    count = 0
    batch_docs: dict[str, dict] = {}
    edges_to_add: list[tuple[str, str, str]] = []

    for i, item in enumerate(tasks):
        # Idempotencia (SSOT): usar el spec_index CANONICO del delta si la
        # proyeccion lo trae; solo fallback al indice de enumerate. Sin esto,
        # un sprint con gaps (spec borrada) re-numeraria los spec_id/doc_id.
        spec_index = item.get("spec_index")
        if spec_index is None:
            spec_index = i
        spec = Spec(
            spring_id=sprint_id,
            objective=item.get("objective", ""),
            domain=item.get("domain", ""),
            spec_type=item.get("type", "feature"),
            spec_index=spec_index,
            result_state=item.get("result_state", ""),
            files=(
                item.get("impacted_artifacts", {}).get("files", [])
                if isinstance(item.get("impacted_artifacts"), dict)
                else []
            ),
            status=item.get("status", "draft"),
            description=item.get("description", ""),
            predicates=item.get("predicates"),
            depends_on=item.get("depends_on", []),
        )
        batch_docs[spec.doc_id] = spec.to_dict()
        count += 1

        node_id = spec.doc_id
        edges_to_add.append((sprint_id, node_id, EDGE_CONTAINS))
        for dep in spec.depends_on or []:
            edges_to_add.append((node_id, f"{sprint_id}:{dep}", EDGE_DEPENDS))
        for fpath in spec.files or []:
            edges_to_add.append((node_id, fpath, EDGE_IMPACTS))

    # Batch persist: 1 serializacion para todos
    if batch_docs:
        cg.store_many("delta", batch_docs)
    for from_id, to_id, edge_type in edges_to_add:
        cg.add_edge(from_id, to_id, type=edge_type)

    return count


# Cores @safe (Result pattern, convention-001): la carga de tasks degrada
# con log observable; el indexado por sprint nunca tumba el index completo.
_index_sprint_safe = safe(index_sprint)


def _load_spring_tasks(chain_dir: str) -> list[dict]:
    """Carga las tasks del DELTA (SpringService) con degradacion observable.

    Result pattern (convention-001): el core @safe nunca lanza; ante fallo
    se loggea warning y se degrada a lista vacia — el indexador no debe
    tumbar un proyecto entero por un sprint con datos corruptos.
    """
    result = _load_spring_tasks_core(chain_dir)
    if is_successful(result):
        return result.value_or([])
    error = result.failure()
    logger.warning(
        "index_sprint: tasks no disponibles para %s (%s: %s); 0 indexadas",
        chain_dir, type(error).__name__, error,
    )
    return []


@safe
def _load_spring_tasks_core(chain_dir: str) -> list[dict]:
    """Core puro de _load_spring_tasks (puede lanzar).

    Reusa SpringService.load_tasks (que resuelve spring_id, garantiza el
    import legacy y degrada con logger.error interno) — un solo punto de
    degradacion, sin duplicar la resolucion.
    """
    from features.delta_tracking.application.services.spring_service import (
        SpringService,
    )

    spring_svc = SpringService.from_chain_dir(chain_dir)
    if not spring_svc:
        return []
    return spring_svc.load_tasks(chain_dir)


def index_all(project_dir: str, cg) -> int:
    """Indexa todos los sprints de un proyecto.

    Args:
        project_dir: Directorio del proyecto
        cg: Instancia de CognitiveGraph

    Returns:
        Numero total de tareas indexadas
    """
    if "delta" not in cg.list_indexes():
        cg.register_defaults()

    total = 0
    # Buscar en estructura directa (project/spring/) y en data/ (project/data/spring/)
    for base_dir in (project_dir, os.path.join(project_dir, "data")):
        for src in ("spring", "delta"):
            src_dir = os.path.join(base_dir, src)
            if not os.path.isdir(src_dir):
                continue
            for sprint_id in sorted(os.listdir(src_dir)):
                sprint_dir = os.path.join(src_dir, sprint_id)
                if os.path.isdir(sprint_dir):
                    result = _index_sprint_safe(sprint_dir, cg)
                    if is_successful(result):
                        total += result.value_or(0)
                    else:
                        error = result.failure()
                        logger.warning(
                            "index_all: sprint %s omitido (%s: %s)",
                            sprint_dir, type(error).__name__, error,
                        )
    return total
