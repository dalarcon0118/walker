"""test_memory_signature_bdd.py — `search_memories` con firma uniforme (AE-INV-03).

Bug medido (2026-11-20):

    search_memories {"project_id": "walker", "ckg": {"text": "..."}}
    -> responses.project_id.error = "Índice 'project_id' no registrado"

El payload llegaba crudo a `search_many` y la plomería del cliente MCP se
interpretaba como un índice. El agente pagaba un turno por una clave que no
es una consulta, y `text` de primer nivel se ignoraba.

Fix: `project_id` (y la paginación) se descartan antes del router; `text` /
`query` de primer nivel son atajo de `ckg.text`; una clave desconocida se
reporta con el ejemplo ejecutable y NO bloquea las consultas válidas.
"""

from __future__ import annotations

from fast_mcp.memories.handlers.knowledge import handle_search_memories


class _CkgFalso:
    def __init__(self) -> None:
        self.recibido: dict | None = None

    def search_many(self, queries: dict) -> dict:
        self.recibido = queries
        return {"responses": {k: {"hits": {"total": 1, "hits": []}} for k in queries}}


class _CtxFalso:
    def __init__(self, ckg: _CkgFalso) -> None:
        self._ckg = ckg

    def ckg(self) -> _CkgFalso:
        return self._ckg


def _run(payload: dict) -> tuple[dict, dict]:
    ckg = _CkgFalso()
    out = handle_search_memories(_CtxFalso(ckg), _payload=payload)
    assert ckg.recibido is not None, "el handler no llegó a buscar"
    return out, ckg.recibido


def test_project_id_no_es_un_indice():
    out, queries = _run(
        {"project_id": "walker", "ckg": {"text": "gate de alcance"}}
    )
    assert "project_id" not in queries
    assert queries["ckg"]["text"] == "gate de alcance"
    assert queries["ckg"]["match_mode"] == "any"
    assert "error" not in out


def test_text_de_primer_nivel_es_atajo_de_ckg():
    _, queries = _run({"project_id": "walker", "text": "engine puro"})
    assert queries == {"ckg": {"text": "engine puro", "match_mode": "any"}}


def test_filtro_estructurado_no_cambia_a_or():
    """`type:decision` es filtro, no pregunta: conserva el contrato AND."""
    _, queries = _run({"ckg": {"type": "decision"}})
    assert queries == {"ckg": {"type": "decision"}}


def test_paginacion_llega_a_cada_indice():
    _, queries = _run(
        {"project_id": "walker", "max_results": 3, "ckg": {"text": "x"}}
    )
    assert queries["ckg"]["size"] == 3


def test_clave_desconocida_falla_ruidoso_pero_parcial():
    out, queries = _run({"ckg": {"text": "x"}, "indiceInventado": {"text": "y"}})
    assert "indiceInventado" in out["error"]
    assert out["indexes"] == ["ckg", "cpg", "delta", "trace"]
    assert out["example"] == '{"ckg": {"text": "gate de alcance"}}'
    assert queries == {"ckg": {"text": "x"}}, "la consulta válida se ejecuta igual"


def test_sin_indices_devuelve_ejemplo_ejecutable():
    ckg = _CkgFalso()
    out = handle_search_memories(_CtxFalso(ckg), _payload={"project_id": "walker"})
    assert "example" in out and "ckg" in out["example"]
    assert ckg.recibido is None, "sin índices no se llama al router"
