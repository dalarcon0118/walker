"""tests/test_recall_knowledge_query_bdd.py — los args de plomería no son filtros.

Bug medido 2026-11-16 en react-native-frontend:

    search_memories {"ckg": {"text": "AppProviders"}}        -> 2 hits
    recall_knowledge {"query": "AppProviders", "max_results": 3} -> 0 hits

El handler copiaba TODO el payload a `query`, incluido `project_id`, y el router
lo trataba como filtro estructurado: caía al path eager y devolvía 0 con
cualquier texto. Mismo síntoma que el bug de trace: "no hay nada" cuando en
realidad la consulta estaba mal formada.
"""

from __future__ import annotations

from fast_mcp.memories.handlers.knowledge import handle_recall_knowledge


class _CkgFalso:
    def __init__(self) -> None:
        self.recibido: dict | None = None

    def search_many(self, queries: dict) -> dict:
        self.recibido = queries
        return {"responses": {k: {"hits": {"total": 0, "hits": []}} for k in queries}}

    def schema(self, index: str) -> dict:
        return {"index": index}


class _CtxFalso:
    def __init__(self, ckg: _CkgFalso) -> None:
        self._ckg = ckg

    def ckg(self) -> _CkgFalso:
        return self._ckg


def _query_del_handler(**kwargs) -> dict:
    ckg = _CkgFalso()
    handle_recall_knowledge(_CtxFalso(ckg), **kwargs)
    assert ckg.recibido is not None, "el handler no llegó a buscar"
    return next(iter(ckg.recibido.values()))


def test_project_id_no_se_cuela_como_filtro():
    q = _query_del_handler(
        project_id="react-native-frontend", query="AppProviders", max_results=3
    )
    assert "project_id" not in q, "project_id es plomería, no un campo del doc"
    assert q["text"] == "AppProviders"
    assert q["max_results"] == 3, "la paginación sí debe llegar al router"


def test_filtros_estructurados_se_conservan():
    q = _query_del_handler(
        project_id="p", _project_id="p", index="trace", event_type="ERROR"
    )
    assert q == {"event_type": "ERROR"}


def test_text_explicito_y_controles_con_underscore_se_descartan():
    q = _query_del_handler(project_id="p", text="AppProviders", _top_k=5, _index="ckg")
    assert q == {"text": "AppProviders"}
