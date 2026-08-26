"""
infrastructure/catalog.py — Catalogo de tareas (LEGACY COMPAT).

⚠️  DEPRECATED desde TASK-003.

Las tareas ahora viven en features/task_execution/tasks/ y side_effects/ con
registro modular via bootstrap.py. Cada modulo exports una
funcion register(registry) que se descubre automaticamente.

Este archivo se mantiene SOLO para compatibilidad con imports
legacy. NO agregues nuevas tareas o schemas aquí.

Reemplazo por modulo:
  - from tasks.planning import ... (en vez de catalog.py)
  - from tasks.gathering import ...
  - etc.
"""

from __future__ import annotations

from typing import Any


# ── Catalog Registry (delega en los nuevos modulos) ──────────────────────────


def register_catalog(registry: Any) -> None:
    """Registra todas las tareas en el TaskRegistry (delega en tasks/)."""
    from ..bootstrap import bootstrap_tasks

    bootstrap_tasks(registry)


def register_side_effects(engine: Any) -> None:
    """Registra todos los side-effects (delega en side_effects/)."""
    from ..bootstrap import bootstrap_side_effects

    bootstrap_side_effects(engine)


def get_task_schema(name: str) -> dict[str, Any] | None:
    """Retorna el schema de entrada/salida de una tarea.

    DEPRECATED: Schema lookup removido del catalogo centralizado.
    Los schemas se definen inline en cada modulo de tasks/ o en los
    estados del workflow. Retorna None siempre (backward compat stub).
    """
    return None


def list_catalog_tasks() -> list[str]:
    """Retorna los nombres de las tareas disponibles.

    DEPRECATED: Usar registry.list_tasks() en su lugar.
    Retorna lista vacia (backward compat stub).
    """
    return []
