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


# ─── ADR-WALKER-084 F0: el payload natural no puede volcar el corpus ────────
# Medido 2026-11-21: `{"ckg":{"query":"metacognición"}}` devolvía 138 docs
# (todo el CKG) porque `query` es una clave META del backend y se descartaba.
# Un filtro no resoluble caía al fallback eager y materializaba el índice.


def test_query_dentro_de_un_indice_es_texto():
    out, queries = _run({"ckg": {"query": "metacognición"}})
    assert queries == {"ckg": {"text": "metacognición"}}, "query es alias de text"
    assert "error" not in out


def test_limit_es_size_y_no_un_filtro_ignorado():
    _, queries = _run({"ckg": {"text": "x", "limit": 3}})
    assert queries["ckg"]["size"] == 3
    assert "limit" not in queries["ckg"]


def test_filtro_desconocido_no_volca_el_indice():
    ckg = _CkgFalso()
    out = handle_search_memories(_CtxFalso(ckg), _payload={"ckg": {"symbol": "x"}})
    assert ckg.recibido is None, "no se busca con un filtro que el schema no declara"
    assert "error" in out
    rejected = out["rejected"]["ckg"]
    assert rejected["unknown"] == ["symbol"]
    assert "text" in rejected["allowed"]
    assert rejected["hits"]["total"] == 0
    assert out["example"] == '{"ckg": {"text": "gate de alcance"}}'


def test_rechazo_parcial_conserva_la_consulta_valida():
    out, queries = _run({"ckg": {"text": "x"}, "trace": {"bogus": 1}})
    assert queries == {"ckg": {"text": "x"}}
    assert "trace" in out["error"]
    assert out["responses"]["trace"]["hits"]["total"] == 0


def test_payloads_de_los_templates_pasan_el_validador():
    """Conformidad template↔handler: el workflow emite estas claves.

    `task-spec.prompt.json` usa `{"trace":{"task":...}}`; los docs de ADR-018
    usan `session_id`. Un validador que rechaza el payload del propio workflow
    es una regresión, no una defensa.
    """
    payloads = [
        {"ckg": {"type": "decision"}},
        {"ckg": {"type": "convention", "scope": "harness"}},
        {"cpg": {"file": "a.py"}},
        {"cpg": {"type": "file", "file": "fast_mcp"}},
        {"delta": {}},
        {"trace": {"task": "SPEC-026"}},
        {"trace": {"type": "TRANSITION"}},
    ]
    for payload in payloads:
        ckg = _CkgFalso()
        out = handle_search_memories(_CtxFalso(ckg), _payload=payload)
        assert ckg.recibido is not None, f"el template quedó rechazado: {payload} → {out}"


def test_trace_task_es_task_id():
    _, queries = _run({"trace": {"task": "SPEC-026"}})
    assert queries == {"trace": {"task_id": "SPEC-026"}}


def test_trace_session_id_rechaza_con_remedio():
    """El trace no indexa la sesión como columna: el error dice qué usar."""
    ckg = _CkgFalso()
    out = handle_search_memories(
        _CtxFalso(ckg), _payload={"trace": {"session_id": "ses_x"}}
    )
    assert ckg.recibido is None
    assert "session_context" in out["rejected"]["trace"]["hint"]

