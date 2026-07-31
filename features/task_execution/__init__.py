"""
features/task_execution/ — Sistema de ejecución de tareas del workflow.

Cada tarea es una función que recibe (ExecutionContext, data) → TaskResult.
Las tareas se registran en el TaskRegistry del engine por nombre.

Los nodos del workflow (workflow.v3/*.node.json) referencian tareas por
su nombre registrado en el campo "action" de cada sub-estado.

Arquitectura:
  domain/    → abstracción de tarea (TaskHandler), ExecutionContext, TaskResult
  tasks/     → implementaciones concretas de tareas (cada una con register())
  bootstrap  → discovery y registro en el engine
"""
from __future__ import annotations

from .domain.models import TaskHandler
from .bootstrap import register_all

__all__ = [
    "TaskHandler",
    "register_all",
]
