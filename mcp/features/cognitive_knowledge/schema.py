"""features/cognitive_knowledge/schema.py — Schema del índice CKG (forFeature, ADR-068).

SSOT: el schema del CKG (Knowledge Graph) vive SOLO aquí (feature dueña).
Reconciliado de las 2 fuentes previas (app/memory_schemas + app/cognitive_registry):
se conserva la versión con fulltext (statement/rationale/tags).
"""
from __future__ import annotations

from typing import Any

# Ranking de severidad para el CKG (obligatoria primero).
SEVERITY_ORDER = {"obligatoria": 0, "recomendada": 1, "informativa": 2}

# Proyección: campos que el LLM recibe al compactar resultados CKG.
CKG_COMPACT_FIELDS = [
    "_id",
    "type",
    "subject",
    "statement",
    "scope",
    "severity",
    "status",
]


def register_ckg_index(graph: Any) -> None:
    """Registra el índice CKG en el storage genérico (una feature, un índice)."""
    graph.register_index(
        "ckg",
        {
            "fields": {
                "type": "type",
                "scope": "scope",
                "label": "statement",
                "task": "metadata.task_id",
                "description": "rationale",
                "tag": "tags",
            },
            "types": {
                "decision": {"description": "Decision de arquitectura"},
                "refactor": {"description": "Refactorizacion aplicada"},
                "convention": {"description": "Convencion de estilo"},
            },
            "description": "CKG: Knowledge Graph - decisiones, convenciones, patrones",
            "search": {
                "type": "fulltext",
                "fields": ["statement", "rationale", "tags"],
            },
        },
    )


# Auto-registro por import (ADR-068): el module.py de la feature importa este
# módulo; al importarlo se registra el schema del CKG. Sin lista central.
from shared.libs.module_system.global_registry import register_memory_index

register_memory_index(register_ckg_index)