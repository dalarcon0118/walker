"""tests/test_outline_instance_scope_bdd.py — identidad de métodos de instancia.

El outline de PureScript emite los N `show` de un módulo con el mismo `name` y
sin scope. El impacto elegía uno arbitrario. tree-sitter expone el bloque
`class_instance` y su `instance_head` (`Show Currency`), que es la identidad
real del método.

Contrato:
  - `_purescript_instance_scopes` devuelve rango + head de cada instancia.
  - `parse_outline` anota `scope` y `qualified_name` del método.
  - `_stable_ids` usa el scope en la clave: ids distintos, deterministas y sin
    sufijos `-2` por orden.
"""

from __future__ import annotations

from fast_mcp.features.code_analysis.application.build_service import _stable_ids
from fast_mcp.features.code_analysis.infrastructure.ast_grep_adapter import (
    _instance_scope_for_line,
    _purescript_instance_scopes,
    parse_outline,
)

_SRC = """module Demo where

data Color = Red | Blue

instance showColor :: Show Color where
  show = case _ of
    Red -> "red"
    Blue -> "blue"

instance eqColor :: Eq Color where
  eq Red Red = true
  eq _ _ = false
"""


def _item(name: str, line: int, signature: str = "") -> dict:
    return {
        "name": name,
        "symbolType": "function",
        "astKind": "function",
        "range": {"start": {"line": line - 1, "column": 0},
                  "end": {"line": line, "column": 0}},
        "signature": signature,
        "isImport": False,
        "isExported": True,
    }


def _write(tmp_path) -> str:
    f = tmp_path / "Demo.purs"
    f.write_text(_SRC)
    return str(f)


def test_scopes_de_instancia(tmp_path):
    scopes = _purescript_instance_scopes(_write(tmp_path))

    assert [s["head"] for s in scopes] == ["Show Color", "Eq Color"]
    assert all(s["start"] <= s["end"] for s in scopes)


def test_scope_por_linea(tmp_path):
    scopes = _purescript_instance_scopes(_write(tmp_path))

    assert _instance_scope_for_line(5, scopes) == "Show Color"
    assert _instance_scope_for_line(11, scopes) == "Eq Color"
    assert _instance_scope_for_line(1, scopes) == ""


def test_parse_outline_anota_scope_y_qualified_name(tmp_path):
    path = _write(tmp_path)

    graph = parse_outline([{"path": path, "items": [
        _item("show", 5),
        _item("eq", 11),
    ]}])

    by_name = {n.name: n for n in graph.nodes.values() if n.type == "function"}
    assert by_name["show"].extra["scope"] == "instance Show Color"
    assert by_name["show"].extra["qualified_name"] == "Show Color.show"
    assert by_name["eq"].extra["scope"] == "instance Eq Color"


def test_stable_ids_distintos_por_scope():
    nodes = {
        "a": {"path": "/repo/src/Demo.purs", "type": "function",
              "name": "show", "line": 5, "scope": "instance Show Color"},
        "b": {"path": "/repo/src/Demo.purs", "type": "function",
              "name": "show", "line": 11, "scope": "instance Show Size"},
        "c": {"path": "/repo/src/Demo.purs", "type": "function",
              "name": "main", "line": 1},
    }

    rekeyed, _ = _stable_ids(nodes, [], "/repo")

    ids = set(rekeyed)
    assert len(ids) == 3
    assert not any(i.endswith("-2") for i in ids), "el scope evita el sufijo por orden"
    # el nodo sin scope conserva el id histórico (clave sin sufijo)
    main_id = [i for i, n in rekeyed.items() if n["name"] == "main"][0]
    expected, _ = _stable_ids(
        {"c": {"path": "/repo/src/Demo.purs", "type": "function",
               "name": "main", "line": 1}}, [], "/repo"
    )
    assert main_id == list(expected)[0]
