"""CognitiveGraph ODM — Object-Document Mapper para CognitiveGraph.

Permite definir entidades con fields y relaciones, y usar patron
ActiveRecord para operaciones CRUD y queries.

Uso:
    from shared.core.cognitive_graph.odm import Entity, fields

    class Task(Entity):
        __index__ = "delta"
        __search__ = ["objective", "description"]

        task_id: str = fields.String()
        objective: str = fields.Text(fulltext=True)
        domain: str = fields.String()
        status: str = fields.String()
        files: list[str] = fields.List()

    # Bind al CognitiveGraph
    Task.bind(cg)

    # Crear
    task = Task(objective="Fix auth bug", domain="security")
    task.save()

    # Buscar
    results = Task.search("auth bug")
    task = Task.get("sprintX:TASK-042")
    results = Task.find_by(domain="security")

    # Relaciones
    task.neighbors("impacts_file")
"""

from .entity import Entity
from . import fields
