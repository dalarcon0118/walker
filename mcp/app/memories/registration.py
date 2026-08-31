"""app/memories/registration.py — Aplicación de los índices de memoria (ADR-068 Fase 2).

NO es dueña de ningún schema (INV-W068-08): las features se auto-registran su
`register_<memoria>_index(graph)` (forFeature). Este módulo:
  - `apply_memory_indexes(graph)` — drena el collector auto-registrado
    (espejo de `_register_module_manifests` para los manifests).
  - `build_cognitive_graph(...)` / `build_searcher(...)` — construcción
    CANÓNICA del grafo (G7): elimina la construcción replicada con doble
    registro (B1/B2) en registry/kg_context/build_service/repos.
  - merge de proyecciones (G4/G5): `COMPACT_FIELDS` de las 4 features +
    `SEVERITY_ORDER` (solo CKG).
"""
from __future__ import annotations

from typing import Any, Callable

from shared.libs.module_system.global_registry import memory_index_registrars
from shared.libs.cognitive_graph import CognitiveGraph
from shared.libs.cognitive_graph.searcher import CognitiveGraphSearcher

# ── Proyección/ranking (SSOT: definidos en las features dueñas, G4/G5) ─────
from features.cognitive_knowledge.schema import CKG_COMPACT_FIELDS, SEVERITY_ORDER
from features.delta_tracking.schema import DELTA_COMPACT_FIELDS
from features.event_tracing.schema import TRACE_COMPACT_FIELDS
from features.code_analysis.schema import CPG_COMPACT_FIELDS

COMPACT_FIELDS: dict[str, list[str]] = {
    "ckg": CKG_COMPACT_FIELDS,
    "delta": DELTA_COMPACT_FIELDS,
    "trace": TRACE_COMPACT_FIELDS,
    "cpg": CPG_COMPACT_FIELDS,
}


def apply_memory_indexes(graph: Any) -> None:
    """Aplica los índices auto-registrados por las features (register_defaults).

    El CONTENIDO lo ponen las features (forFeature, R6); esto solo las drena
    (INV-W068-08). Cada feature registra SOLO su índice → sin conflictos (G1).
    """
    for registrar in memory_index_registrars():
        registrar(graph)


def build_cognitive_graph(
    chain_dir: str = "",
    conn: Any = None,
    backend: Any = None,
    register_defaults: Callable[[Any], None] | None = None,
) -> CognitiveGraph:
    """Construcción CANÓNICA del CognitiveGraph con los índices del walker.

    G7: UNA sola función de construcción (reemplaza la replicación con doble
    registro en registry/kg_context/build_service/repos). Si no se provee
    conn, se resuelve desde chain_dir (memory_data_dir → load_memory_connection).
    """
    if conn is None and chain_dir:
        from shared.libs.cognitive_graph.config import load_memory_connection
        from app.path_resolver import memory_data_dir

        conn = load_memory_connection(memory_data_dir(chain_dir))
    return CognitiveGraph(
        chain_dir,
        backend=backend,
        conn=conn,
        register_defaults=register_defaults or apply_memory_indexes,
        compact_fields=COMPACT_FIELDS,
        severity_order=SEVERITY_ORDER,
    )


def build_searcher(chain_dir: str = "", graph: Any = None) -> CognitiveGraphSearcher:
    """Lector CQRS canónico sobre el grafo compuesto (capa de lectura)."""
    if graph is None:
        graph = build_cognitive_graph(chain_dir)
    return CognitiveGraphSearcher(graph)