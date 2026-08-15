"""KG query tasks — thin adapter (delegates to kg_helpers + CkgApplicationService).

kg.py expone el registro de tasks para el discovery de task_execution.
La lógica de negocio vive en kg_helpers.py y CkgApplicationService.
"""
from __future__ import annotations

from features.main.task_execution.tasks.kg_helpers import (
    kg_agent_context,
    kg_multi,
    kg_schema,
    kg_search,
    documenting_knowledge,
)
from features.main.task_execution.tasks.kg_metadata import KG_TASKS


def register(registry: Any) -> None:
    for name, fn, meta in KG_TASKS:
        registry.register(name, fn, metadata=meta)
