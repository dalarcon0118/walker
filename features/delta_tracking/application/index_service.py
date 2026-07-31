"""application/index_service.py — Indexacion de sprints via Task(entity).save().

Usa la entidad Task (ODM) para almacenar tareas directamente en el grafo
de CognitiveGraph con TokenIndex para busqueda full-text y edges para relaciones.
"""

from __future__ import annotations
import os


def index_sprint(chain_dir: str, cg) -> int:
    """Indexa las tareas de un sprint en CognitiveGraph via Task(entity).save().

    Lee datos via SprintStateRepository (SSOT) y crea entidades Task
    que se almacenan como nodos en el grafo delta de CognitiveGraph.
    El ODM maneja automaticamente el TokenIndex (full-text) y los edges.

    Args:
        chain_dir: Ruta al directorio del sprint
        cg: Instancia de CognitiveGraph

    Returns:
        Numero de tareas indexadas
    """
    from features.spring.infrastructure.repositories import SprintStateRepository
    from features.delta_tracking.entities import Task

    # Asegurar que el indice delta esta registrado con schema ODM
    if "delta" not in cg.list_indexes():
        Task.bind(cg)

    state = SprintStateRepository().load_dict(chain_dir)
    tasks = state.get("tasks", [])
    if not tasks:
        return 0

    sprint_id = os.path.basename(os.path.normpath(chain_dir))
    count = 0

    for item in tasks:
        tid = item.get("task_id", "?")
        if not tid or tid == "?":
            continue

        # Crear entidad Task (el save() usa __id_field__ = "task_id")
        task = Task(
            task_id=tid,
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
        task.save()
        count += 1

        # Crear edges en el grafo para relaciones
        node_id = f"{sprint_id}:{tid}"

        for dep in item.get("depends_on", []):
            cg.add_edge(node_id, f"{sprint_id}:{dep}", type="depends_on")

        files = (
            item.get("impacted_artifacts", {}).get("files", [])
            if isinstance(item.get("impacted_artifacts"), dict)
            else []
        )
        for fpath in files:
            cg.add_edge(node_id, fpath, type="impacts_file")

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
