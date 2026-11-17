"""tests/test_collector_knowledge_bdd.py — vocabulario y APIs del collector.

Dos bugs medidos el 2026-11-16 en `prompts/collector.py`:

1. Vocabulario inexistente: pedía `type: "quality"` (el schema solo declara
   decision/refactor/convention/pattern/rule/component) y tags
   "global"/"architecture", que ningún proyecto usa. En las 7 memorias
   registradas los types reales son decision/design-decision/implementation*/
   verification/fix.
2. Interfaz equivocada: asumía un searcher ES-like ({hits: {hits: [...]}}) pero
   `CodeKnowledgeGraph.search` devuelve una lista y `search_many` devuelve
   {responses: {...}} → AttributeError silencioso → contexto vacío.

Efecto post-fix (react-native-frontend, k=implement): architecture sí,
patterns 5, quality 2, global 2, relevant 23.
"""

from __future__ import annotations

from fast_mcp.features.workflow.application.prompts.collector import (
    KNOWLEDGE_QUERIES,
    TASK_TYPE_ALIASES,
    collect_knowledge,
    collect_project_info,
)


class _SearcherPorRespuestas:
    """Interfaz real: CodeKnowledgeGraph.search_many({index: query})."""

    def __init__(self, docs: dict[str, list[dict]]) -> None:
        self.docs = docs
        self.consultas: list[dict] = []

    def search_many(self, queries: dict) -> dict:
        # queries = {"ckg": {"type": "..."}}. Se registran TODAS.
        query = next(iter(queries.values()))
        self.consultas.append(query)
        clave = next((v for k, v in query.items() if k != "text"), "")
        docs = self.docs.get(f"{next(iter(query))}:{clave}", [])
        hits = [{"_id": d["_id"], "_index": "ckg", "_source": d} for d in docs]
        return {"responses": {"ckg": {"hits": {"total": len(hits), "hits": hits}}}}


class _SearcherLista:
    """`CodeKnowledgeGraph.search` devuelve lista de docs."""

    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs

    def search(self, index: str, query: dict) -> list[dict]:
        return self.docs


def test_types_canonicos_no_incluyen_valores_inexistentes():
    """El vocabulario del collector debe cubrir los types que los proyectos
    realmente guardan (medido: decision/design-decision/implementation*/
    verification/fix)."""
    tipos = {
        q["type"] for qs in KNOWLEDGE_QUERIES.values() for q in qs if "type" in q
    }
    for real in ("decision", "design-decision", "pattern", "component",
                 "convention", "rule", "verification"):
        assert real in tipos, f"el collector no cubre el type real {real!r}"
    # `quality` como type no existe en ningún proyecto: no debe pedirse.
    assert "quality" not in tipos


def test_normalize_type_canonicaliza_aliases():
    from fast_mcp.features.cognitive_knowledge.application.services.ckg_common import (
        normalize_type,
    )

    assert normalize_type("Design Decision") == "design-decision"
    assert normalize_type("Implementation State") == "implementation_state"
    assert normalize_type("  Decision  ") == "decision"
    assert normalize_type(42) == "decision"
    assert normalize_type("") == "decision"


def test_union_dedupe_y_respeta_el_contrato_de_salida():
    docs = [
        {"_id": "d1", "statement": "decision de diseño"},
        {"_id": "d2", "statement": "patrón de capas"},
    ]
    searcher = _SearcherPorRespuestas({"type:design-decision": docs, "type:pattern": docs})
    info = collect_project_info("x", ckg_searcher=searcher)
    assert info["architecture"] == "decision de diseño"
    # patterns une design-decision + pattern sin duplicar por _id
    assert sorted(info["patterns"]) == ["decision de diseño", "patrón de capas"]


def test_relevant_usa_alias_del_nodo():
    docs = [{"_id": "i1", "statement": "implementación verificada"}]
    searcher = _SearcherPorRespuestas({"type:implementation": docs})
    kn = collect_knowledge("x", {"node_id": "implement"}, ckg_searcher=searcher)
    assert kn["relevant"] == [{"_id": "i1", "statement": "implementación verificada"}]
    planos = [f"{k}:{v}" for q in searcher.consultas for k, v in q.items()]
    assert "type:implementation" in planos
    assert "type:implementation_state" in planos


def test_interfaz_lista_del_ckg_no_lanza():
    searcher = _SearcherLista([{"_id": "z1", "statement": "desde lista"}])
    info = collect_project_info("x", ckg_searcher=searcher)
    assert info["architecture"] == "desde lista"


def test_sin_searcher_degrada_vacio():
    assert collect_project_info("x", ckg_searcher=None)["patterns"] == []
    assert collect_knowledge("x", ckg_searcher=None)["global"] == []
