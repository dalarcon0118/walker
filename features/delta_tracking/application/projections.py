"""application/projections.py — Proyecciones de Spec (read models legacy).

Funciones PURAS que convierten entidades Spec al formato de dict "task"
que consumen el engine, los use cases de spring y el CLI (campos
task_id/spec_id, impacted_artifacts.files, predicates, ...).

Extraídas de SpringService (SRP): el mapeo legacy vivía en
get_specs_legacy/load_tasks mezclando presentación con orquestación.
"""

from __future__ import annotations

import logging

from returns.pipeline import is_successful
from returns.result import safe

from features.delta_tracking.domain.entities import Spec

logger = logging.getLogger(__name__)


def spec_to_task_dict(spec: Spec) -> dict:
    """Proyecta una Spec al formato legacy de task.

    Mantiene los campos canónicos (spec_id/spec_index) que los
    reindexadores usan para reconstruir el doc_id idempotente, y expone
    impacted_artifacts.files como convención del engine.
    """
    return {
        "task_id": spec.spec_id,
        "spec_id": spec.spec_id,
        "spec_index": spec.spec_index,
        "objective": spec.objective,
        "domain": spec.domain,
        "type": spec.spec_type,
        "status": spec.status,
        "result_state": spec.result_state,
        "description": spec.description,
        "impacted_artifacts": {"files": spec.files},
        "predicates": spec.predicates or {},
        "depends_on": spec.depends_on or [],
    }


def specs_as_tasks(specs: list[Spec]) -> list[dict]:
    """Proyecta una lista de specs al formato legacy de tasks."""
    return [spec_to_task_dict(s) for s in specs]


# ── Orquestacion de lectura (tasks desde chain_dir) ──────────────


def load_spring_tasks(spring_svc, chain_dir: str) -> list[dict]:
    """Tasks del sprint desde el DELTA (fuente unica, ADR-WALKER-013).

    Orquesta: spring_id desde el chain_dir + import legacy garantizado +
    proyeccion legacy. Result pattern (convention-001): el core @safe nunca
    lanza; ante fallo se loggea error y se degrada a lista vacia — el
    engine recibe tasks vacias pero el fallo queda registrado.
    """
    if not chain_dir:
        return []
    result = _load_spring_tasks_core(spring_svc, chain_dir)
    if is_successful(result):
        return result.value_or([])
    error = result.failure()
    logger.error(
        "load_tasks fallo para %s (%s: %s); tasks vacias",
        chain_dir, type(error).__name__, error,
    )
    return []


@safe
def _load_spring_tasks_core(spring_svc, chain_dir: str) -> list[dict]:
    """Core puro de load_spring_tasks (puede lanzar)."""
    spring_id = spring_svc.get_spring_id(chain_dir)
    if not spring_id:
        return []
    spring_svc.ensure_spring_imported(chain_dir, spring_id)
    return spring_svc.get_specs_legacy(spring_id)
