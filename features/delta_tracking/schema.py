"""features/delta_tracking/schema.py — Schema del índice Delta (forFeature, ADR-068).

SSOT: el schema del DeltaGraph (specs y springs) vive SOLO aquí (feature dueña).
Reconciliado de las 2 fuentes previas (idénticas; se conserva el fulltext).
"""
from __future__ import annotations

from typing import Any

# Proyección: campos que el LLM recibe al compactar resultados Delta.
DELTA_COMPACT_FIELDS = [
    "_id",
    "spec_id",
    "spring_id",
    "objective",
    "status",
    "domain",
    "result_state",
]


def register_delta_index(graph: Any) -> None:
    """Registra el índice Delta en el storage genérico (una feature, un índice)."""
    graph.register_index(
        "delta",
        {
            "fields": {
                "task": "spec_id",
                "spec": "spec_id",
                "spring": "spring_id",
                "domain": "domain",
                "type": "type",
            },
            "types": {"spec": {}, "spring": {}},
            "description": "DeltaGraph: specs y springs del proyecto",
            "search": {
                "type": "fulltext",
                "fields": ["objective", "title", "description", "domain", "result_state"],
            },
        },
    )


# Auto-registro por import (ADR-068): el module.py de la feature importa este
# módulo; al importarlo se registra el schema del Delta. Sin lista central.
from shared.libs.module_system.global_registry import register_memory_index

register_memory_index(register_delta_index)