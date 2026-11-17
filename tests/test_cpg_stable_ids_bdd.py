"""test_cpg_stable_ids_bdd.py — ids determinísticos y merge sin erosión.

Problema medido (2026-11-17): cada sync incremental dejaba MENOS aristas que
antes (14.825 → 12.823; CALLS 10.329 → 8.346) y el fingerprint decía
"up_to_date". Dos causas:

1. `_rekey_scanned` asignaba ids con un nonce por corrida: re-escanear un
   archivo cambiaba TODOS sus ids, así que el merge descartaba las aristas
   entrantes y no las regeneraba.
2. `scan_files` emitía `file -> módulo` como CALLS además del IMPORTS; el
   storage deduplica por (from, to) ignorando el type, así que el IMPORTS se
   perdía en cada re-scan (4 aristas por archivo importador).

Los ids ahora salen de (path relativo, type, name), sin la línea: mover código
no cambia el id.
"""

from __future__ import annotations

from pathlib import Path

from fast_mcp.features.code_analysis.application.build_service import (
    _merge_scanned,
    _stable_ids,
)
from fast_mcp.features.code_analysis.infrastructure.ast_grep_adapter import scan_files


def _node(path: str, type_: str, name: str, line: int = 1) -> dict:
    return {"id": "viejo", "path": path, "type": type_, "name": name, "line": line}


def test_ids_son_deterministicos_entre_corridas():
    base = "/repo"
    nodes = {"cpg-0001": _node("/repo/a.py", "function", "f")}
    first, _ = _stable_ids(dict(nodes), [], base)
    second, _ = _stable_ids(dict(nodes), [], base)

    assert list(first) == list(second)
    assert list(first)[0].startswith("cpg-")


def test_mover_lineas_no_cambia_el_id():
    base = "/repo"
    before, _ = _stable_ids({"cpg-0001": _node("/repo/a.py", "function", "f", 10)}, [], base)
    after, _ = _stable_ids({"cpg-0001": _node("/repo/a.py", "function", "f", 40)}, [], base)

    assert list(before) == list(after)


def test_duplicados_del_mismo_nombre_reciben_sufijo_estable():
    base = "/repo"
    nodes = {
        "cpg-0001": _node("/repo/a.py", "method", "__init__", 5),
        "cpg-0002": _node("/repo/a.py", "method", "__init__", 20),
    }
    ids, _ = _stable_ids(nodes, [], base)

    assert len(ids) == 2
    assert sorted(ids)[-1].endswith("-2")


def test_aristas_se_remaplean_y_deduplican_por_tipo():
    base = "/repo"
    nodes = {
        "cpg-0001": _node("/repo/a.py", "file", "a.py"),
        "cpg-0002": _node("/repo/a.py", "module", "json"),
    }
    edges = [
        {"from": "cpg-0001", "to": "cpg-0002", "type": "IMPORTS"},
        {"from": "cpg-0001", "to": "cpg-0002", "type": "IMPORTS"},
        {"from": "cpg-0001", "to": "cpg-9999", "type": "CALLS"},  # externo
    ]

    _, fixed = _stable_ids(nodes, edges, base)

    assert len(fixed) == 2, "duplicado exacto descartado"
    assert fixed[1]["to"] == "cpg-9999", "destino externo se conserva"


def test_merge_conserva_arista_entrante_a_simbolo_que_sigue_existiendo():
    """El caso que se perdía: A (sin cambios) → B (re-escaneado)."""
    old = {
        "nodes": {
            "a": {"id": "a", "path": "/repo/a.py", "type": "function", "name": "f"},
            "b": {"id": "b", "path": "/repo/b.py", "type": "function", "name": "g"},
        },
        "edges": [{"from": "a", "to": "b", "type": "CALLS"}],
    }
    new_nodes = {"b": {"id": "b", "path": "/repo/b.py", "type": "function", "name": "g"}}
    new_edges = [{"from": "b", "to": "a", "type": "CALLS"}]

    merged = _merge_scanned(old, new_nodes, new_edges,
                            rescanned_ids={"b"}, removed_ids=set())

    assert {"from": "a", "to": "b", "type": "CALLS"} in merged["edges"], (
        "la entrante desde un archivo no re-escaneado debe sobrevivir"
    )
    assert {"from": "b", "to": "a", "type": "CALLS"} in merged["edges"]


def test_merge_borra_las_aristas_de_un_simbolo_eliminado():
    old = {
        "nodes": {
            "a": {"id": "a", "path": "/repo/a.py", "type": "function", "name": "f"},
            "gone": {"id": "gone", "path": "/repo/b.py", "type": "function", "name": "z"},
        },
        "edges": [{"from": "a", "to": "gone", "type": "CALLS"}],
    }

    merged = _merge_scanned(old, {}, [], rescanned_ids={"gone"}, removed_ids={"gone"})

    assert merged["edges"] == []


def test_scan_no_duplica_import_como_call(tmp_path: Path):
    """`file -> módulo` debe ser IMPORTS y solo IMPORTS (el dedupe es por from/to)."""
    src = tmp_path / "a.py"
    src.write_text("import json\n\nprint(json.dumps({}))\n")

    graph = scan_files([str(src)])
    file_ids = {nid for nid, n in graph.nodes.items() if n.type == "file"}
    module_ids = {nid for nid, n in graph.nodes.items() if n.type == "module"}

    calls_file_to_module = [
        e for e in graph.edges
        if e.type == "CALLS" and e.from_id in file_ids and e.to_id in module_ids
    ]
    imports = [e for e in graph.edges if e.type == "IMPORTS"]

    assert imports, "el import debe quedar como IMPORTS"
    assert calls_file_to_module == [], (
        "un import no es una llamada: el CALLS file→módulo hacía que el storage "
        "descartara el IMPORTS al deduplicar por (from, to)"
    )
