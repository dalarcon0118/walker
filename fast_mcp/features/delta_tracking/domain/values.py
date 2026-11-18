"""domain/values.py — Value objects and constants for delta tracking.

Spring and Spec are the two node types of the DeltaGraph.
Edges model business relationships: contains, impacts, depends.
"""
from __future__ import annotations

import os

# ── Status flows ──────────────────────────────────────────────────

SPRING_STATUS_FLOW = ["planning", "active", "paused", "reviewing", "closed"]

# ADR-WALKER-078: el ORDEN de la lista ya no es la regla de transición. `paused`
# es reversible (active <-> paused), cosa que un índice lineal no puede expresar.
# Grafo explícito; `closed` es terminal y `reviewing` sigue siendo solo-forward.
SPRING_TRANSITIONS: dict[str, frozenset[str]] = {
    "planning": frozenset({"active", "reviewing", "closed"}),
    "active": frozenset({"paused", "reviewing", "closed"}),
    "paused": frozenset({"active", "reviewing", "closed"}),
    "reviewing": frozenset({"closed"}),
    "closed": frozenset(),
}
SPEC_STATUS_FLOW = [
    "draft",
    "specified",
    "approved",
    "in_progress",
    "in_review",
    "completed",
    "rejected",
    "backlog",
]


def is_valid_spring_status(status: str) -> bool:
    return status in SPRING_STATUS_FLOW


def is_valid_spec_status(status: str) -> bool:
    return status in SPEC_STATUS_FLOW


# ── Node types ────────────────────────────────────────────────────

TYPE_SPRING = "spring"
TYPE_SPEC = "spec"
TYPE_SESSION = "session"

# ── Edge types ────────────────────────────────────────────────────

EDGE_CONTAINS = "contains"  # spring → spec
EDGE_IMPACTS = "impacts_file"  # spec → file
EDGE_DEPENDS = "depends_on"  # spec → spec


# ── ID helpers ────────────────────────────────────────────────────

def spec_doc_id(spring_id: str, spec_id: str) -> str:
    """Global unique ID: {spring_id}:{spec_id}."""
    return f"{spring_id}:{spec_id}"


def spec_id_from_index(index: int) -> str:
    """SPEC-001..N from base-0 index."""
    return f"SPEC-{index + 1:03d}"


def spring_id_from_chain_dir(chain_dir: str) -> str:
    """Derive spring_id from chain_dir basename.

    The directory name IS the spring_id.
    """
    return os.path.basename(os.path.normpath(chain_dir))
