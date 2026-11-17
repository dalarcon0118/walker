"""test_cyclo_bdd.py — complejidad real en el CPG (antes era 0 fijo).

`Node.cyclo` existía como campo y se indexaba en None en TODOS los nodos, así
que `metrics` reportaba `cyclo_avg=0.0` y el gate de complejidad no podía
dispararse nunca. Medido 2026-11-17 en backend-microservices: 769 funciones,
`cyclo` 0 en todas.

McCabe: 1 + puntos de decisión del cuerpo.
"""

from __future__ import annotations

from fast_mcp.features.code_analysis.infrastructure.ast_grep_adapter import (
    _cyclo_by_line,
    parse_outline,
)

FUENTE = '''def simple(a):
    return a + 1


def ramificado(x):
    if x > 0:
        if x > 10:
            return "alto"
        return "medio"
    for i in range(x):
        if i % 2:
            continue
    try:
        return 1 / x
    except ZeroDivisionError:
        return 0
    except ValueError:
        return -1
'''


def _write(tmp_path, name="m.py", text=FUENTE):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def test_funcion_lineal_tiene_complejidad_uno(tmp_path):
    cyclo = _cyclo_by_line(_write(tmp_path))

    assert cyclo[1] == 1


def test_cuenta_if_for_y_except(tmp_path):
    # 1 base + 2 if + 1 for + 1 if interno del for + 2 except = 7
    cyclo = _cyclo_by_line(_write(tmp_path))

    assert cyclo[5] == 7


def test_booleanos_y_ternarios_suman(tmp_path):
    src = "def f(a, b):\n    return a if a and b else b or 1\n"
    cyclo = _cyclo_by_line(_write(tmp_path, "b.py", src))

    # 1 + IfExp + dos BoolOp (uno aporta 1, el otro 1)
    assert cyclo[1] == 4


def test_lenguaje_sin_parser_devuelve_vacio(tmp_path):
    p = tmp_path / "x.ts"
    p.write_text("export function f(a) { return a }\n")

    assert _cyclo_by_line(str(p)) == {}, "mejor 'no disponible' que un cero que parece dato"


def test_archivo_inexistente_no_rompe(tmp_path):
    assert _cyclo_by_line(str(tmp_path / "nope.py")) == {}


def test_parse_outline_asigna_cyclo_al_nodo(tmp_path):
    """El nodo tiene que salir con cyclo, no con None."""
    path = _write(tmp_path)

    graph = parse_outline([{
        "path": path,
        "items": [
            {"symbolType": "function", "name": "simple", "range": {"start": {"line": 0}}},
            {"symbolType": "function", "name": "ramificado", "range": {"start": {"line": 4}}},
        ],
    }])

    by_name = {n.name: n for n in graph.nodes.values() if n.type == "function"}
    assert by_name["simple"].cyclo == 1
    assert by_name["ramificado"].cyclo == 7
