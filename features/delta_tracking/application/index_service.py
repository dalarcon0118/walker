"""application/index_service.py — Indexacion de sprints via Task(entity).save().

Usa la entidad Task (ODM) para almacenar tareas directamente en el grafo
de CognitiveGraph con TokenIndex para busqueda full-text y edges para relaciones.
"""

from __future__ import annotations
import os


def index_sprint(chain_dir: str, cg) -> int:
    """Indexa las tareas de un sprint en CognitiveGraph via Task(entity).save().

    Lee las specs del DELTA (SpringService — fuente unica) y crea
    entidades Task que se almacenan como nodos en el grafo delta de
    CognitiveGraph. El ODM maneja automaticamente el TokenIndex (full-text)
    y los edges.

    PRINCIPIO estructural: las specs del spring viven en el DeltaGraph;
    sprint-state.json y plan.json fueron eliminados (ADR-WALKER-013/014).

    Args:
        chain_dir: Ruta al directorio del sprint
        cg: Instancia de CognitiveGraph

    Returns:
        Numero de tareas indexadas
    """
    from features.delta_tracking.entities import Task

    # Asegurar que el indice delta esta registrado con schema ODM
    if "delta" not in cg.list_indexes():
        Task.bind(cg)

    tasks = []
    try:
        from features.delta_tracking.application.services.spring_service import (
            SpringService,
        )

        spring_svc = SpringService.from_chain_dir(chain_dir)
        if spring_svc:
            spring_id = spring_svc.get_spring_id(chain_dir)
            spring_svc.ensure_spring_imported(chain_dir, spring_id)
            tasks = spring_svc.get_specs_legacy(spring_id)
    except Exception:
        tasks = []
    if not tasks:
        return 0

    sprint_id = os.path.basename(os.path.normpath(chain_dir))
    count = 0
    batch_docs: dict[str, dict] = {}
    edges_to_add: list[tuple[str, str, str]] = []

    for item in tasks:
        tid = item.get("task_id", "") or item.get("spec_id", "?")
        if not tid or tid == "?":
            continue

        unique_id = f"{sprint_id}:{tid}"

        task = Task(
            task_id=unique_id,
            sprint_id=sprint_id,
            objective=item.get("objective", ""),
            title=item.get("title", ""),
            description=item.get("description", ""),
            domain=item.get("domain", ""),
            result_state=item.get("result_state", ""),
            task_type=item.get("type", ""),
            status=item.get("status", ""),
            files=(
                item.get("impacted_artifacts", {}).get("files", [])
                if isinstance(item.get("impacted_artifacts"), dict)
                else []
            ),
        )
        task._cg_instance = cg
        batch_docs[unique_id] = dict(task._data)
        count += 1

        node_id = f"{sprint_id}:{tid}"
        for dep in item.get("depends_on", []):
            edges_to_add.append((node_id, f"{sprint_id}:{dep}", "depends_on"))
        files = (
            item.get("impacted_artifacts", {}).get("files", [])
            if isinstance(item.get("impacted_artifacts"), dict)
            else []
        )
        for fpath in files:
            edges_to_add.append((node_id, fpath, "impacts_file"))

    # Batch persist: 1 serializacion para todos
    if batch_docs:
        cg.store_many("delta", batch_docs)
    for from_id, to_id, edge_type in edges_to_add:
        cg.add_edge(from_id, to_id, type=edge_type)

    return count


def index_all(project_dir: str, cg) -> int:
    """Indexa todos los sprints de un proyecto.

    Args:
        project_dir: Directorio del proyecto
        cg: Instancia de CognitiveGraph

    Returns:
        Numero total de tareas indexadas
    """
    from features.delta_tracking.entities import Task

    if "delta" not in cg.list_indexes():
        Task.bind(cg)

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
                    try:
                        total += index_sprint(sprint_dir, cg)
                    except Exception:
                        continue
    return total
