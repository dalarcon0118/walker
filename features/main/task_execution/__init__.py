"""
features/task_execution/ — Sistema de ejecución de tareas del workflow.

Cada tarea es una función que recibe (ExecutionContext, data) → TaskResult.
Las tareas se registran en el TaskRegistry del engine por nombre.

Los nodos del workflow (workflow.v3/*.node.json) referencian tareas por
su nombre registrado en el campo "action" de cada sub-estado.

Arquitectura (SPEC-014 Fase 3):
  tasks/       → implementaciones concretas (cada una con register())
  bootstrap    → discovery y registro en el engine

TaskHandler (ABC) eliminado (INV-014): la abstracción de tarea muerta no
tenía usos — las tareas son funciones (ctx, data) → TaskResult registradas
por nombre. La capa de binding es el ActionCatalog (SPEC-014 F1).
"""
from __future__ import annotations

from .bootstrap import register_all

__all__ = [
    "register_all",
]
