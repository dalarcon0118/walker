"""
features/spring/ — Módulo de dominio Sprint.

Spring es un CASO DE USO del workflow engine. Gestiona sprints,
tareas de backlog, planificación y ejecución.

PRINCIPIO: El workflow engine NO sabe de spring. Solo sabe de nodos,
transiciones, gates y contratos (BPMN-like). Spring es el "negocio"
que el workflow orquesta sin conocer.

Relación:
  - Los nodos en workflow.v3/ declaran action: "spec_task" (por ejemplo)
  - El engine busca "spec_task" en el TaskRegistry
  - La función spec_task vive en scripts/tasks/planning.py
  - El engine NO sabe que eso es "spring" — solo ejecuta la tarea

  - Los nodos declaran context.sources: [{provider: "sprint", ...}]
  - El engine busca "sprint" en el ContextProviderRegistry
  - SprintProvider vive en features/spring/context/providers.py
  - El engine NO sabe qué datos devuelve — solo los compone para el LLM
"""
from __future__ import annotations

from .domain.models import SprintState
from .infrastructure.repositories import SprintStateRepository

__all__ = [
    "SprintState",
    "SprintStateRepository",
]
