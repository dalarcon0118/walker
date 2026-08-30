"""KG query tasks — thin adapter (delegates to kg_helpers + CkgApplicationService).

kg.py expone el registro de tasks para el discovery de task_execution.
La lógica de negocio vive en kg_helpers.py y CkgApplicationService.
"""
from __future__ import annotations

from tasks.actions.knowledge_helpers import (
    kg_agent_context,
    search_memories,
    memory_schema,
    recall_knowledge,
    documenting_knowledge,
)
from tasks.actions.knowledge_metadata import KG_TASKS


def register(registry: Any) -> None:
    for name, fn, meta in KG_TASKS:
        registry.register(name, fn, metadata=meta)
