"""entities.py — Entidad Task para DeltaGraph (indice delta).

Uso:
    from features.delta_tracking.entities import Task

    Task.bind(cg)
    tasks = Task.search("autenticacion")
    task = Task.get("sprintX:TASK-042")
    task.files  # navega edges impacts_file
"""

from __future__ import annotations
from libs.cognitive_graph.odm import Entity, fields


class Task(Entity):
    """DeltaGraph: tareas del sprint con busqueda full-text y relaciones."""

    __index__ = "delta"
    __id_field__ = "task_id"
    __search__ = ["objective", "title", "description", "domain", "result_state"]

    task_id: str = fields.String()
    objective: str = fields.Text(fulltext=True)
    title: str = fields.Text(fulltext=True)
    description: str = fields.Text(fulltext=True)
    domain: str = fields.String()
    result_state: str = fields.String()
    task_type: str = fields.String()
    status: str = fields.String()
    sprint_id: str = fields.String()
    files: list[str] = fields.List()

    # ── Accesores de relacion desde el grafo ──

    def get_files(self, cg=None):
        """Retorna los archivos impactados por esta tarea (via impacts_file edges)."""
        return self.neighbors(edge_type="impacts_file", cg=cg)

    def get_dependencies(self, cg=None):
        """Retorna las tareas de las que depende (via depends_on edges)."""
        return self.neighbors(edge_type="depends_on", cg=cg)

    def get_dependents(self, cg=None):
        """Retorna las tareas que dependen de esta (via depends_on incoming)."""
        instance = cg or self._get_cg()
        if instance is None or not self._id:
            return []
        idx = instance._indexes.get(self.__index__)
        if not idx:
            return []
        # Incoming edges: la tarea actual es el destino (to == self._id)
        return [
            {"from": e.get("from"), "rel": e.get("type", "depends_on")}
            for e in idx.get_edges(self._id, direction="in")
            if e.get("type") == "depends_on"
        ]
