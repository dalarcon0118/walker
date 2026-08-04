"""DeltaTracking domain — Entidades y value objects del DeltaGraph.

Spring, Spec, valores de estado, tipos de edge.
"""
from libs.cognitive_graph.models import TokenIndex, IndexEntry
from features.delta_tracking.domain.entities import Spring, Spec
from features.delta_tracking.domain.values import (
    TYPE_SPRING, TYPE_SPEC,
    EDGE_CONTAINS, EDGE_IMPACTS, EDGE_DEPENDS,
    SPRING_STATUS_FLOW, SPEC_STATUS_FLOW,
    spec_doc_id, spec_id_from_index,
)

__all__ = [
    "TokenIndex", "IndexEntry",
    "Spring", "Spec",
    "TYPE_SPRING", "TYPE_SPEC",
    "EDGE_CONTAINS", "EDGE_IMPACTS", "EDGE_DEPENDS",
    "SPRING_STATUS_FLOW", "SPEC_STATUS_FLOW",
    "spec_doc_id", "spec_id_from_index",
]
