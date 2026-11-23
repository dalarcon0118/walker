"""tests/test_outline_symbol_dedupe_bdd.py — un símbolo = un nodo.

El outline de PureScript emite dos items por símbolo anotado: la firma de tipo
(`astKind: signature`) y la definición (`astKind: function`). Cada uno creaba
su nodo con id distinto y las CALLS se partían: medido 2026-11-22 en
client-chat, 455 pares (path, name) duplicados, 480 nodos extra y
`routeToPath` con 3 callers en un nodo y 0 en el otro.

Contrato:
  - firma + definición → queda solo la definición.
  - solo firma → se conserva (es el único símbolo que hay).
  - dos definiciones reales → se conservan ambas (no se oculta un redefinido).
  - el orden de aparición se mantiene.
"""

from __future__ import annotations

from fast_mcp.features.code_analysis.infrastructure.ast_grep_adapter import (
    _dedupe_outline_items,
    parse_outline,
)


def _item(name: str, ast: str = "function", line: int = 10) -> dict:
    return {
        "name": name,
        "symbolType": "function",
        "astKind": ast,
        "range": {
            "start": {"line": line - 1, "column": 0},
            "end": {"line": line, "column": 0},
        },
        "signature": f"{name} :: T",
        "isImport": False,
        "isExported": True,
    }


def test_descarta_la_firma_si_hay_definicion():
    items = [_item("routeToPath", "signature", 36), _item("routeToPath", "function", 37)]

    out = _dedupe_outline_items(items)

    assert len(out) == 1
    assert out[0]["astKind"] == "function"
    assert out[0]["range"]["start"]["line"] == 36  # línea de la definición (0-based 36)


def test_conserva_la_firma_si_no_hay_definicion():
    items = [_item("sessionEnv", "signature", 101)]

    out = _dedupe_outline_items(items)

    assert len(out) == 1
    assert out[0]["astKind"] == "signature"


def test_no_oculta_dos_definiciones_reales():
    items = [_item("foo", "function", 10), _item("foo", "function", 20)]

    out = _dedupe_outline_items(items)

    assert [i["range"]["start"]["line"] for i in out] == [9, 19]


def test_conserva_el_orden_de_aparicion():
    items = [
        _item("a", "signature", 1), _item("b", "signature", 2),
        _item("a", "function", 3), _item("b", "function", 4),
    ]

    out = _dedupe_outline_items(items)

    assert [i["name"] for i in out] == ["a", "b"]
    assert [i["range"]["start"]["line"] for i in out] == [2, 3]


def test_parse_outline_crea_un_nodo_por_simbolo(tmp_path):
    src = tmp_path / "Route.purs"
    src.write_text("module Route where\n\nrouteToPath :: Route -> String\nrouteToPath = identity\n")

    graph = parse_outline([{
        "path": str(src),
        "items": [
            _item("routeToPath", "signature", 3),
            _item("routeToPath", "function", 4),
        ],
    }])

    functions = [n for n in graph.nodes.values() if n.type == "function"]
    assert len(functions) == 1
    contains = [e for e in graph.edges if e.type == "CONTAINS"]
    assert len(contains) == 1
