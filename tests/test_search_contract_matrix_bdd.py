"""tests/test_search_contract_matrix_bdd.py — matriz de contrato por índice.

Barrido del modo "cero silencioso": una consulta mal formada debe fallar o
advertir, nunca devolver `total: 0` como si el dato no existiera.

Cuatro bugs del mismo tipo medidos el 2026-11-16 (todos respondían "no hay"):
  1. ckg con cualquier hit     → AttributeError `self.self` (buscaba y rompía).
  2. identificador camelCase   → `"app"* "providers"*` no matchea "AppProviders".
  3. `trace` con `text`        → sin fulltext, eager devolvía 0.
  4. `recall_knowledge`        → `project_id` como filtro → eager → 0.

Esta matriz fija, por índice del schema de producción:
  - texto: encuentra el término si hay fulltext; falla ruidoso si no lo hay;
  - estructurado: el campo declarado filtra; el combinado text+filtro avisa;
  - filtro NO declarado: la respuesta trae `note` con el motivo del fallback.
"""

from __future__ import annotations

import pytest

from fast_mcp.shared_kernel.cognitive_graph import CognitiveGraph
from fast_mcp.shared_kernel.cognitive_graph.backends import resolve_storage_backend
from fast_mcp.shared_kernel.cognitive_graph.connection import MemoryConnection
from fast_mcp.shared_kernel.cognitive_graph.schemas import PRODUCTION_INDEX_SCHEMAS
from fast_mcp.shared_kernel.cognitive_graph.storage_errors import StorageContractError

# Un doc por índice con un término único, para poder afirmar "el texto encuentra
# EXACTAMENTE lo sembrado" sin depender del contenido del proyecto.
SEED = {
    "ckg": {"c:1": {"statement": "ZetaComponente bootstrap", "type": "component"}},
    "delta": {"d:1": {"objective": "ZetaComponente objetivo", "type": "spec",
                      "spec_id": "s-1"}},
    "cpg": {"p:1": {"name": "zeta_componente", "path": "/x/zeta.py",
                    "type": "function", "kind": "function"}},
    "trace": {"t:1": {"event_type": "ZetaComponente", "tool": "x", "node": "n"}},
}
TERM = "ZetaComponente"


def _graph(tmp_path) -> tuple[CognitiveGraph, object]:
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    backend = resolve_storage_backend(conn)
    for name in PRODUCTION_INDEX_SCHEMAS:
        backend.register_index(name, PRODUCTION_INDEX_SCHEMAS[name])
        backend.store_many(name, SEED[name])
    return CognitiveGraph("", conn=conn, backend=backend), backend


def _has_fulltext(index: str) -> bool:
    search = PRODUCTION_INDEX_SCHEMAS[index].get("search") or {}
    return bool(search.get("fields"))


def test_matriz_texto_por_indice(tmp_path):
    _, backend = _graph(tmp_path)
    for index in PRODUCTION_INDEX_SCHEMAS:
        if _has_fulltext(index):
            total = backend.search(index, {"text": TERM})["hits"]["total"]
            assert total == 1, f"{index}: el texto debe encontrar lo sembrado"
        else:
            with pytest.raises(StorageContractError, match="no declara fulltext"):
                backend.search(index, {"text": TERM})


def test_matriz_filtro_declarado(tmp_path):
    """Cada índice filtra por un campo de su mapping sin tocar el fallback."""
    _, backend = _graph(tmp_path)
    casos = {
        "ckg": ({"type": "component"}, 1),
        "delta": ({"type": "spec"}, 1),
        "cpg": ({"type": "function"}, 1),
        "trace": ({"event_type": TERM}, 1),
    }
    for index, (query, esperado) in casos.items():
        result = backend.search(index, query)
        assert result["hits"]["total"] == esperado, f"{index}: filtro declarado"
        assert "note" not in result, f"{index}: no debe caer al fallback eager"


def test_filtro_no_declarado_avisa_en_vez_de_cero_mudo(tmp_path):
    """Un campo que el schema no declara resuelve por scan, pero lo DICE."""
    _, backend = _graph(tmp_path)
    result = backend.search("ckg", {"no_such_field": "x"})
    assert result["hits"]["total"] == 0
    assert "note" in result, "un 0 por filtro no declarado no puede ser mudo"
    assert "no_such_field" in result["note"]
    assert result["eager_docs"] >= 1


def test_combinado_text_mas_filtro_no_declarado_avisa(tmp_path):
    _, backend = _graph(tmp_path)
    result = backend.search("ckg", {"text": TERM, "no_such_field": "x"})
    assert result["hits"]["total"] == 0
    assert "note" in result and "combinada" in result["note"]


def test_search_many_propaga_el_aviso(tmp_path):
    """Federado: el `note` llega al cliente, no solo al log del server."""
    cg, _ = _graph(tmp_path)
    out = cg.search_many({"ckg": {"no_such_field": "x"}})
    assert "note" in out["responses"]["ckg"]


def test_filtro_por_tag_es_array_y_resuelve_por_sql(tmp_path):
    """`tag` (lista `tags`) no tiene columna escalar: se resuelve con
    json_each y NO debe caer al fallback eager.

    El collector del workflow lo usa 5 veces por prompt; cada caída al eager
    materializaba el índice completo (medido: 293 docs por consulta).
    """
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    backend = resolve_storage_backend(conn)
    backend.register_index("ckg", PRODUCTION_INDEX_SCHEMAS["ckg"])
    backend.store_many(
        "ckg",
        {
            "c:1": {"statement": "a", "type": "pattern", "tags": ["global", "arch"]},
            "c:2": {"statement": "b", "type": "pattern", "tags": ["arch"]},
            "c:3": {"statement": "c", "type": "pattern"},
        },
    )

    result = backend.search("ckg", {"tag": "global"})
    assert result["hits"]["total"] == 1
    assert "note" not in result, "tag debe resolverse por SQL, no por eager"
    assert result["hits"]["hits"][0]["_id"] == "c:1"

    # la intersección de dos tags no matchea: el filtro es por pertenencia
    assert backend.search("ckg", {"tag": "nope"})["hits"]["total"] == 0
