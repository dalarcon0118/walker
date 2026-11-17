"""tests/test_prompt_manager_knowledge_bdd.py — enriquecimiento CKG de prompts.

`collector.py` se restauró del motor heredado (commit 9ee4112) pero nadie lo
llamaba: el PromptManager recibía el contexto del caller y no lo enriquecía,
así que el conocimiento del proyecto nunca llegaba al prompt (el design doc
`prompts-management` describe ese paso y no existía).

El cableado es opt-in por plantilla: el manager agrega las claves
`knowledge_*`/`project_*`, el renderer ignora las que la plantilla no usa, y
por eso ningún prompt existente cambia. Eso último se verifica byte a byte.
"""

from __future__ import annotations

from fast_mcp.features.workflow.application.prompts.manager import PromptManager


class _SearcherFalso:
    def __init__(self, docs: dict[str, list[dict]]) -> None:
        self.docs = docs
        self.consultas: list[dict] = []

    def search_many(self, queries: dict) -> dict:
        query = next(iter(queries.values()))
        self.consultas.append(query)
        clave = next((v for k, v in query.items() if k != "text"), "")
        docs = self.docs.get(clave, [])
        hits = [{"_id": d["_id"], "_index": "ckg", "_source": d} for d in docs]
        return {"responses": {"ckg": {"hits": {"total": len(hits), "hits": hits}}}}


def _searcher() -> _SearcherFalso:
    return _SearcherFalso(
        {
            "convention": [{"_id": "k1", "statement": "Convención global"}],
            "rule": [{"_id": "k2", "statement": "Gate de complejidad"}],
            "design-decision": [{"_id": "k3", "statement": "Capas estrictas"}],
            "implementation": [{"_id": "k4", "statement": "Implementación previa"}],
        }
    )


def test_sin_searcher_no_agrega_claves(tmp_path):
    m = PromptManager(chain_dir=str(tmp_path))
    data = m._build_data({"node_id": "implement"}, None)  # noqa: SLF001
    assert not any(k.startswith("knowledge_") for k in data)
    assert data == {"node_id": "implement"}


def test_con_searcher_agrega_conocimiento_como_texto(tmp_path):
    m = PromptManager(chain_dir=str(tmp_path), ckg_searcher=_searcher())
    data = m._build_data({"node_id": "implement"}, None)  # noqa: SLF001

    assert data["knowledge_global"] == "- Convención global\n- Gate de complejidad"
    assert data["knowledge_architecture"] == "- Capas estrictas"
    assert data["knowledge_relevant"] == "- Implementación previa"
    assert all(isinstance(data[k], str) for k in data if k.startswith("knowledge_"))


def test_el_contexto_del_caller_gana(tmp_path):
    m = PromptManager(chain_dir=str(tmp_path), ckg_searcher=_searcher())
    data = m._build_data({"knowledge_global": "mío"}, None)  # noqa: SLF001
    assert data["knowledge_global"] == "mío"


def test_render_de_plantilla_existente_es_identico(tmp_path):
    """Sin placeholders nuevos, el prompt renderizado NO cambia una coma."""
    contexto = {"task_id": "T-1", "task_title": "arreglar algo"}
    sin = PromptManager(chain_dir=str(tmp_path)).resolve(
        "design/design-review", context=dict(contexto)
    )
    con = PromptManager(chain_dir=str(tmp_path), ckg_searcher=_searcher()).resolve(
        "design/design-review", context=dict(contexto)
    )
    assert sin == con, "el enriquecimiento no debe alterar plantillas que no lo usan"


def test_plantilla_opt_in_renderiza_el_conocimiento(tmp_path):
    m = PromptManager(chain_dir=str(tmp_path), ckg_searcher=_searcher())
    data = m._build_data({"node_id": "implement"}, None)  # noqa: SLF001
    out = m._renderer.render("Reglas:\n{knowledge_global}", data)  # noqa: SLF001
    assert "Convención global" in out


def test_el_conocimiento_se_recorta_para_no_inflar_el_prompt(tmp_path):
    """Sin tope, react-native aportaba 9.294 chars en `knowledge_relevant`."""
    from fast_mcp.features.workflow.application.prompts.manager import (
        MAX_KNOWLEDGE_CHARS,
        MAX_KNOWLEDGE_ITEMS,
        _bullets,
    )

    docs = [{"statement": f"hecho {i} " + "x" * 400} for i in range(20)]
    out = _bullets(docs)
    assert out.count("- ") <= MAX_KNOWLEDGE_ITEMS
    assert len(out) <= MAX_KNOWLEDGE_CHARS
    assert out.endswith("…")
