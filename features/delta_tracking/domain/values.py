"""domain/values.py — Value objects y constantes del dominio Delta.

Spring y Spec son los dos tipos de nodo del DeltaGraph.
Los edges modelan relaciones de negocio: contiene, impacta, depende.
"""
from __future__ import annotations

# ── Flujos de estado ──────────────────────────────────────────────

SPRING_STATUS_FLOW = ["planning", "active", "reviewing", "closed"]
SPEC_STATUS_FLOW = [
    "draft",
    "specified",
    "in_progress",
    "completed",
    "rejected",
    "backlog",
]


def is_valid_spring_status(status: str) -> bool:
    return status in SPRING_STATUS_FLOW


def is_valid_spec_status(status: str) -> bool:
    return status in SPEC_STATUS_FLOW


# ── Tipos de nodo ─────────────────────────────────────────────────

TYPE_SPRING = "spring"
TYPE_SPEC = "spec"

# ── Tipos de edge ─────────────────────────────────────────────────

EDGE_CONTAINS = "contains"      # spring → spec
EDGE_IMPACTS = "impacts_file"   # spec → file
EDGE_DEPENDS = "depends_on"     # spec → spec

# ── Formatos de ID ────────────────────────────────────────────────

def spec_doc_id(spring_id: str, spec_id: str) -> str:
    """ID global unico: {spring_id}:{spec_id}."""
    return f"{spring_id}:{spec_id}"


def spec_id_from_index(index: int) -> str:
    """SPEC-001..N desde indice base-0."""
    return f"SPEC-{index + 1:03d}"
