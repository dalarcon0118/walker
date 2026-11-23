"""tests/test_cpg_call_resolution_bdd.py — resolución de CALLS sin cruzar lenguajes.

Bug medido 2026-11-22 en client-chat: `_resolve_callee_node` tomaba `cands[0]`,
el primer nodo con el mismo nombre, sin mirar lenguaje ni módulo. Resultado:
44 aristas falsas (JS `start` → `Headless.start`, JS `error` → `Log.error`,
`min` de JS → `min` de un script Python) y `Log.info` con 25 usos reales
marcado como huérfano.

Contrato:
  - Nunca se resuelve un callee de OTRO lenguaje (purs/py ≠ js).
  - js/jsx/ts/tsx sí se resuelven entre sí (FFI JS→TS del mismo proyecto).
  - Se prefiere mismo archivo, luego mismo directorio, luego ruta menor.
  - Sin candidato compatible → nodo `external_call` en el archivo del caller.
"""

from __future__ import annotations

from fast_mcp.features.code_analysis.application.build_service import (
    BuildGraphUseCase,
    _lang_family,
)


def _nid(fp: str, name: str) -> str:
    return f"cpg:{name}@{fp}"


def _nodes(*specs: tuple[str, str]) -> tuple[dict, dict]:
    """(cpg_nodes, nodes_by_name) desde [(path, name), ...]."""
    cpg: dict = {}
    by_name: dict[str, list[str]] = {}
    for path, name in specs:
        nid = _nid(path, name)
        cpg[nid] = {"id": nid, "type": "function", "name": name, "path": path}
        by_name.setdefault(name, []).append(nid)
    return cpg, by_name


def _resolve(cpg, by_name, callee, fp, line=10):
    return BuildGraphUseCase._resolve_callee_node(
        callee, by_name, cpg, _nid, fp, line
    )


def test_familias_js_comparten_runtime():
    assert _lang_family("javascript") == "js"
    assert _lang_family("jsx") == "js"
    assert _lang_family("typescript") == "js"
    assert _lang_family("tsx") == "js"
    assert _lang_family("purescript") == "purescript"
    assert _lang_family("python") == "python"


def test_js_no_resuelve_a_purescript():
    ps = "/repo/src/Shared/Libs/Tea/Headless.purs"
    fp = "/repo/src/UI/DS/Native.js"
    cpg, by_name = _nodes((ps, "start"))

    got = _resolve(cpg, by_name, "start", fp)

    assert got != _nid(ps, "start")
    assert cpg[got]["kind"] == "external_call"
    assert cpg[got]["path"] == fp


def test_python_no_resuelve_a_purescript():
    ps = "/repo/src/Data/Log.purs"
    fp = "/repo/scripts/logger/manager.py"
    cpg, by_name = _nodes((ps, "error"))

    got = _resolve(cpg, by_name, "error", fp)

    assert got != _nid(ps, "error")
    assert cpg[got]["kind"] == "external_call"


def test_js_si_resuelve_a_typescript_del_mismo_proyecto():
    ts = "/repo/src/Data/db.ts"
    fp = "/repo/src/Data/WalletStore.js"
    cpg, by_name = _nodes((ts, "migrate"))

    got = _resolve(cpg, by_name, "migrate", fp)

    assert got == _nid(ts, "migrate")


def test_prefiere_el_mismo_archivo():
    fp = "/repo/src/Data/WalletStore.js"
    other = "/repo/src/Data/other.js"
    cpg, by_name = _nodes((other, "migrate"), (fp, "migrate"))

    got = _resolve(cpg, by_name, "migrate", fp)

    assert got == _nid(fp, "migrate")


def test_prefiere_el_mismo_directorio():
    fp = "/repo/src/Data/WalletStore.js"
    near = "/repo/src/Data/store-helpers.js"
    far = "/repo/src/Features/Wallet/helpers.js"
    cpg, by_name = _nodes((far, "migrate"), (near, "migrate"))

    got = _resolve(cpg, by_name, "migrate", fp)

    assert got == _nid(near, "migrate")


def test_desempate_determinista_por_ruta():
    fp = "/repo/src/Data/WalletStore.js"
    a = "/repo/src/Data/a-helpers.js"
    b = "/repo/src/Data/b-helpers.js"
    cpg, by_name = _nodes((b, "migrate"), (a, "migrate"))

    got = _resolve(cpg, by_name, "migrate", fp)

    assert got == _nid(a, "migrate")


def test_nombre_calificado_conserva_el_filtro_de_lenguaje():
    fp = "/repo/src/App.js"
    ps = "/repo/src/Data/Array.purs"
    cpg, by_name = _nodes((ps, "map"))

    got = _resolve(cpg, by_name, "Data.Array.map", fp)

    assert got != _nid(ps, "map")
    assert cpg[got]["kind"] == "external_call"


# ── métodos de instancia: identidad por scope ───────────────────────────


def _scoped(path: str, name: str, scope: str, line: int) -> dict:
    nid = f"cpg:{name}@{scope}@{path}"
    return {
        "id": nid, "type": "function", "name": name, "path": path,
        "line": line, "scope": f"instance {scope}",
        "qualified_name": f"{scope}.{name}",
    }


def test_varias_instancias_no_eligen_una_arbitraria():
    model = "/repo/src/Model.purs"
    view = "/repo/src/View.purs"
    a = _scoped(model, "show", "Show A", 5)
    b = _scoped(model, "show", "Show B", 15)
    cpg = {a["id"]: a, b["id"]: b}
    by_name = {"show": [a["id"], b["id"]]}

    got = _resolve(cpg, by_name, "show", view)

    assert got not in (a["id"], b["id"])
    assert cpg[got]["kind"] == "external_call"
    assert cpg[got]["ambiguous"] is True
    assert sorted(cpg[got]["ambiguous_candidates"]) == ["Show A.show", "Show B.show"]


def test_dos_instancias_en_el_mismo_archivo_son_ambiguas():
    fp = "/repo/src/Model.purs"
    a = _scoped(fp, "show", "Show A", 5)
    b = _scoped(fp, "show", "Show B", 15)
    cpg = {a["id"]: a, b["id"]: b}
    by_name = {"show": [a["id"], b["id"]]}

    got = _resolve(cpg, by_name, "show", fp)

    assert cpg[got]["ambiguous"] is True


def test_una_sola_instancia_se_resuelve():
    model = "/repo/src/Model.purs"
    a = _scoped(model, "show", "Show A", 5)
    cpg = {a["id"]: a}
    by_name = {"show": [a["id"]]}

    got = _resolve(cpg, by_name, "show", "/repo/src/View.purs")

    assert got == a["id"]


# ── externos: atribución por archivo ────────────────────────────────────


def test_externo_se_reutiliza_solo_en_el_mismo_archivo():
    fp = "/repo/src/A.js"
    other = "/repo/src/B.js"
    ext = {"id": "ext-b", "type": "function", "name": "pure",
           "path": other, "kind": "external_call"}
    cpg = {"ext-b": ext}
    by_name = {"pure": ["ext-b"]}

    got = _resolve(cpg, by_name, "pure", fp)

    assert got != "ext-b"
    assert cpg[got]["path"] == fp


def test_externo_del_mismo_archivo_se_reutiliza():
    fp = "/repo/src/A.js"
    ext = {"id": "ext-a", "type": "function", "name": "pure",
           "path": fp, "kind": "external_call"}
    cpg = {"ext-a": ext}
    by_name = {"pure": ["ext-a"]}

    got = _resolve(cpg, by_name, "pure", fp)

    assert got == "ext-a"
