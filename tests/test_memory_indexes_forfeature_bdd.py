"""tests/test_memory_indexes_forfeature_bdd.py — Completitud de índices forFeature (ADR-068 G9).

Valida el patrón forFeature (R6): cada memoria se registra desde SU feature
dueña y el conjunto completo {ckg, cpg, delta, trace} (+ refs) se aplica vía
la construcción canónica (G7) sin doble registro (B1/B2).
"""
from __future__ import annotations

import pytest

from shared.libs.module_system.global_registry import (
    memory_index_registrars,
    register_memory_index,
    reset_memory_indexes,
)
from app.memories.registration import COMPACT_FIELDS, apply_memory_indexes, build_cognitive_graph

EXPECTED = {"ckg", "cpg", "delta", "trace"}
EXPECTED_PROJECTIONS = {"ckg", "cpg", "delta", "trace"}


@pytest.fixture(scope="session", autouse=True)
def _features_auto_registered():
    """Importa los module.py de las features UNA vez (registran sus schemas).

    El auto-registro ocurre al importar (module.py → schema.py →
    register_memory_index). En producción el boot importa una vez y nunca
    resetea; aquí se limpia al final de la sesión.
    """
    import features.code_analysis.module  # noqa: F401
    import features.cognitive_knowledge.module  # noqa: F401
    import features.delta_tracking.module  # noqa: F401
    import features.event_tracing.module  # noqa: F401

    yield
    reset_memory_indexes()


class TestMemoryIndexesForFeature:
    """Completitud del registro de índices por feature dueña (R6)."""

    def test_las_4_features_se_auto_registran(self):
        registrars = memory_index_registrars()
        assert len(registrars) >= 4, f"falta un registrar: {len(registrars)}"
        assert len(registrars) == len(set(registrars)), "duplicados en el collector (G2)"

    def test_graph_tiene_los_5_indices(self, tmp_path):
        graph = build_cognitive_graph(str(tmp_path))
        indexes = set(graph.list_indexes())
        missing = EXPECTED - indexes
        assert not missing, f"faltan índices: {missing} (B1/B2: sin doble registro)"
        # refs (cpg-ref-*) son dinámicos por proyecto — no obligatorios aquí.

    def test_cada_indice_tiene_su_proyeccion(self):
        missing_proj = EXPECTED_PROJECTIONS - set(COMPACT_FIELDS.keys())
        assert not missing_proj, f"proyecciones faltantes: {missing_proj} (G4)"

    def test_dedupe_del_collector(self):
        from features.cognitive_knowledge.schema import register_ckg_index

        before = len(memory_index_registrars())
        register_memory_index(register_ckg_index)  # re-registro (idempotente)
        assert len(memory_index_registrars()) == before, "G2: dedupe falló"

    def test_apply_memory_indexes_idempotente(self, tmp_path):
        graph = build_cognitive_graph(str(tmp_path))
        apply_memory_indexes(graph)  # segunda aplicación (idempotente)
        indexes = set(graph.list_indexes())
        missing = EXPECTED - indexes
        assert not missing, f"segunda aplicación perdió índices: {missing}"