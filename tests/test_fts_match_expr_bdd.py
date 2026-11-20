"""tests/test_fts_match_expr_bdd.py — recall de identificadores camelCase.

Bug medido 2026-11-16: el índice FTS guarda "AppProviders" como UN token,
pero `tokenize()` parte la consulta en [app, providers] y `match_expr` unía
los prefijos con AND. Resultado: el identificador era inalcanzable.

    consulta                     FTS crudo   con match_expr (antes)
    "AppProviders"                     2          0
    "parseNumbersFromText" (delta)     1          0
    "TypeScript"                       4          3

El fix agrega el token LITERAL en OR con el grupo de prefijos, solo cuando la
palabra se partió. Las búsquedas multi-término conservan el AND estricto.
"""

from __future__ import annotations

from fast_mcp.shared_kernel.cognitive_graph.backends import resolve_storage_backend
from fast_mcp.shared_kernel.cognitive_graph.connection import MemoryConnection
from fast_mcp.shared_kernel.cognitive_graph.fts import match_expr
from fast_mcp.shared_kernel.cognitive_graph.schemas import PRODUCTION_INDEX_SCHEMAS


def test_camelcase_agrega_el_token_literal_en_or():
    expr = match_expr("AppProviders")
    assert expr == '("AppProviders" OR ("app"* "providers"*))'


def test_snake_y_dash_tambien_se_recuperan():
    assert '"parseNumbersFromText"' in match_expr("parseNumbersFromText")
    assert '"sprint_backend"' in match_expr("sprint_backend")


def test_palabra_simple_no_cambia():
    """Sin split no hay OR: el prefijo ya cubre el recall."""
    assert match_expr("bootstraps") == '"bootstraps"*'


def test_multi_palabra_mantiene_and_estricto():
    assert match_expr("auth token") == '"auth"* "token"*'
    assert match_expr("") == ""


def test_mode_any_une_los_prefijos_con_or():
    """Recall explícito para preguntas naturales (F2, 2026-11-20)."""
    assert match_expr("auth token", mode="any") == '("auth"* OR "token"*)'
    # Un solo token es idéntico en ambos modos.
    assert match_expr("bootstraps", mode="any") == '"bootstraps"*'


def test_fts_real_cae_a_or_cuando_el_and_no_encuentra(tmp_path):
    """El AND de 2 tokens no matchea; el match_mode=any + bm25 sí lo encuentra."""
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    backend = resolve_storage_backend(conn)
    backend.register_index("ckg", PRODUCTION_INDEX_SCHEMAS["ckg"])
    backend.store_many(
        "ckg",
        {"c:1": {"statement": "sprint delta model", "type": "decision"}},
    )

    # AND exacto: ambos tokens están en el doc → hit (precisión intacta).
    assert backend.search("ckg", {"text": "sprint delta"})["hits"]["total"] == 1
    # Sin match_mode, un token ausente NO matchea (contrato AND del índice).
    assert backend.search("ckg", {"text": "almacenamiento sprint"})["hits"]["total"] == 0
    # Con match_mode=any (texto libre del CKG), el OR lo recupera.
    result = backend.search(
        "ckg", {"text": "almacenamiento sprint", "match_mode": "any"}
    )
    assert result["hits"]["total"] == 1


def test_scoped_valida_columnas_y_anida_el_or():
    expr = match_expr("AppProviders", ["statement"], ["statement"])
    assert expr == 'statement:("AppProviders" OR ("app"* "providers"*))'
    assert match_expr("AppProviders", ["no-existe"], ["statement"]) == ""


def test_fts_real_encuentra_el_identificador(tmp_path):
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    backend = resolve_storage_backend(conn)
    backend.register_index("ckg", PRODUCTION_INDEX_SCHEMAS["ckg"])
    backend.store_many(
        "ckg",
        {"c:1": {"statement": "AppProviders bootstraps app", "type": "component"}},
    )

    assert backend.search("ckg", {"text": "AppProviders"})["hits"]["total"] == 1
    # column-scoped (sintaxis col:(...)) también resuelve
    assert backend.search(
        "ckg", {"text": {"query": "AppProviders", "fields": ["statement"]}}
    )["hits"]["total"] == 1
    # multi-término sigue siendo AND: un token ausente no matchea
    assert backend.search("ckg", {"text": "bootstraps zzz"})["hits"]["total"] == 0


def test_comilla_interna_no_rompe_la_sintaxis_match(tmp_path):
    """El texto del usuario no puede inyectar operadores FTS vía el literal."""
    expr = match_expr('App"Providers')
    assert expr == '("AppProviders" OR ("app"* "providers"*))'

    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    backend = resolve_storage_backend(conn)
    backend.register_index("ckg", PRODUCTION_INDEX_SCHEMAS["ckg"])
    backend.store_many("ckg", {"c:1": {"statement": "AppProviders bootstraps app",
                                       "type": "component"}})
    # ejecuta la consulta real: si la sintaxis fuera inválida, lanzaría
    assert backend.search("ckg", {"text": 'App"Providers'})["hits"]["total"] == 1


def test_match_mode_any_cuando_el_and_devuelve_pocos(tmp_path):
    """AND con 1–4 candidatos no alcanza: se completa con OR + bm25.

    Regresión medida 2026-11-20: al indexar el cuerpo de los ADR, el AND
    empezó a devolver 1 doc irrelevante y el fallback (sólo con total==0)
    dejaba de dispararse. "Encontrar algo" no es encontrar lo preguntado.
    """
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    backend = resolve_storage_backend(conn)
    backend.register_index("ckg", PRODUCTION_INDEX_SCHEMAS["ckg"])
    backend.store_many(
        "ckg",
        {
            "c:1": {"statement": "engine e infraestructura juntos", "type": "decision"},
            "c:2": {"statement": "engine puro", "type": "decision"},
            "c:3": {"statement": "engine sin adaptadores", "type": "decision"},
            "c:4": {"statement": "infraestructura del storage", "type": "decision"},
            "c:5": {"statement": "infraestructura del trace", "type": "decision"},
            "c:6": {"statement": "infraestructura del cpg", "type": "decision"},
        },
    )

    # Sin match_mode: contrato AND (sólo el doc que tiene ambos tokens).
    assert backend.search("ckg", {"text": "engine infraestructura"})["hits"]["total"] == 1
    # Con match_mode=any y AND corto: OR recupera el corpus relevante.
    amplio = backend.search(
        "ckg", {"text": "engine infraestructura", "match_mode": "any"}
    )
    assert amplio["hits"]["total"] == 6

