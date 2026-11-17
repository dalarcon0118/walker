"""test_fast_mcp_perf_fixes_bdd.py — Regresión de los fixes de rendimiento.

Cubre las dos causas raíz de los timeouts de las tools MCP de análisis
(memoria 11884):

  1. _walk_code_files (build_service.py): el walk del pipeline CPG recorría
     node_modules completo (91K+ archivos) sin podar .walkerignore/IGNORED_DIRS
     → horas de CPU dentro del event loop del server MCP → todas las tools
     expiraban (-32001).
  2. TokenIndex._keys (cognitive_graph/models.py): dedup O(1) con conjunto
     (token, node_id, field) — antes add() hacía scan lineal de entries[token]
     → O(n²) acumulado en re-index masivo (37K nodos × tokens comunes).
"""

from __future__ import annotations

import os

import pytest


# ── 1. _walk_code_files: poda de directorios ignorados ──────────────


def _make_project(base) -> str:
    """Árbol de proyecto con node_modules, directorio ignorado y código.

    base: str o pathlib.Path del directorio raíz donde crear 'proj'.
    """
    from pathlib import Path

    root = Path(base) / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def main():\n    pass\n")
    (root / "src" / "ui.ts").write_text("export const x = 1;\n")
    # node_modules: el caso real que quemaba CPU (91K+ archivos)
    (root / "node_modules" / "pkg" / "dist").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "dist" / "index.js").write_text("() => 1")
    # directorio ignorado via .walkerignore
    (root / "vendor" / "lib").mkdir(parents=True)
    (root / "vendor" / "lib" / "legacy.py").write_text("import sys")
    # archivos no-código
    (root / "README.md").write_text("# hola")
    (root / "assets").mkdir()
    (root / "assets" / "logo.bin").write_bytes(b"\x00\x01\x02")
    (root / "data").mkdir()
    (root / "data" / "cache.json").write_text("{}")
    return str(root)


def test_walk_code_files_prunes_node_modules_y_ignorados():
    """El walk no desciende a node_modules ni respeta .walkerignore."""
    from fast_mcp.features.code_analysis.application.build_service import (
        _walk_code_files,
    )
    from fast_mcp.shared_kernel.project_support.watcher import IgnoreRules

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = _make_project(os.path.join(td, "x"))
        # vendor/ ignorado por .walkerignore — escribir ANTES de construir
        # IgnoreRules (las reglas se cargan en __init__)
        walkignore = os.path.join(root, ".walkerignore")
        with open(walkignore, "w") as f:
            f.write("vendor/\n")
        ignore = IgnoreRules(root)

        files = _walk_code_files([root], ignore)

        rels = {os.path.relpath(f, root) for f in files}
        assert any(
            r.endswith("app.py") for r in rels
        ), "el código fuente debe incluirse"
        assert any(
            r.endswith("ui.ts") for r in rels
        ), "el código TypeScript debe incluirse"
        assert not any("node_modules" in r for r in rels), (
            "node_modules NO debe recorrerse (causa raíz del timeout -32001)"
        )
        assert not any("vendor" in r for r in rels), (
            "los directorios del .walkerignore NO deben incluirse"
        )
        # .md/.json son lenguajes conocidos (markdown/json en SSOT _LANG_MAP)
        # y SÍ entran; un binario .bin NO (fuera de _LANG_MAP → no es código).
        assert not any(r.endswith(".bin") for r in rels), (
            "extensiones desconocidas NO entran al walk (filtro _detect_lang)"
        )


def test_walk_code_files_prune_ignored_dirs_inline(tmp_path):
    """Podado por IGNORED_DIRS (dist/build/.git...) aun sin .walkerignore."""
    from fast_mcp.features.code_analysis.application.build_service import (
        _walk_code_files,
    )

    root = tmp_path / "proj"
    (root / "dist" / "bundle").mkdir(parents=True)
    (root / "dist" / "bundle" / "out.js").write_text("1")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("x")
    (root / "main.py").write_text("print(1)")

    files = _walk_code_files([str(root)], None)
    rels = {os.path.relpath(f, str(root)) for f in files}
    assert "main.py" in rels
    assert not any("dist" in r for r in rels)
    assert not any(".git" in r for r in rels)


def test_ignore_rules_directorio_matchea_descendientes(tmp_path):
    """`patron/` ignora el directorio Y todo lo que cuelga, a cualquier nivel.

    Regresión medida 2026-11-16: con `docs/` en el .walkerignore,
    `is_ignored("docs/spec.md")` daba False; solo el podado de directorios
    del walk salvaba el caso, así que un consumidor que filtre por archivo
    indexaba el directorio ignorado sin avisar.
    """
    from fast_mcp.shared_kernel.project_support.watcher import IgnoreRules

    root = tmp_path / "proj"
    root.mkdir()
    (root / ".walkerignore").write_text(
        "docs/\nbuild/\n*.pyc\nnode_modules/\n"
    )
    ignore = IgnoreRules(str(root))

    # patrón de directorio: a sí mismo, a sus hijos y a cualquier profundidad
    assert ignore.is_ignored("docs/") is True
    assert ignore.is_ignored("docs/spec.md") is True
    assert ignore.is_ignored("docs/a/b/c.md") is True
    assert ignore.is_ignored("sub/build/out.js") is True
    assert ignore.is_ignored("node_modules/react/index.js") is True

    # patrón sin barra: vale en la raíz y a cualquier profundidad
    assert ignore.is_ignored("cache.pyc") is True
    assert ignore.is_ignored("pkg/deep/cache.pyc") is True

    # código real no se toca (ni por prefijo ni por basename)
    assert ignore.is_ignored("experimental/src/auth/config.py") is False
    assert ignore.is_ignored("scripts/start.sh") is False
    assert ignore.is_ignored("docs_oldish/readme.md") is False


def test_walk_code_files_rejects_str_path(tmp_path):
    """Un `str` en raw_paths debe fallar ruidoso, no recorrer el filesystem.
    los caracteres de la ruta; "/" es `isdir` → el walk se iba al filesystem
    entero y la tool colgaba >240s sin error (solo salía por timeout externo).
    """
    import pytest

    from fast_mcp.features.code_analysis.application.build_service import (
        _walk_code_files,
    )

    root = tmp_path / "proj"
    (root).mkdir()
    (root / "main.py").write_text("print(1)")

    with pytest.raises(TypeError, match="SECUENCIA de rutas"):
        _walk_code_files(str(root), None)

    # La forma correcta (lista) sigue funcionando.
    assert any(
        f.endswith("main.py") for f in _walk_code_files([str(root)], None)
    )


# ── 2. TokenIndex: dedup O(1) vía _keys ─────────────────────────────


def test_token_index_add_dedup_o1():
    """add() de (token, node_id, field) repetido crea UNA sola entrada —
    antes era scan lineal O(n²) en entries[token]."""
    from fast_mcp.shared_kernel.cognitive_graph.models import IndexEntry, TokenIndex

    idx = TokenIndex()
    entry = IndexEntry(node_id="node-1", field="name", snippet="Sprint")

    idx.add("sprint", entry)
    idx.add("sprint", entry)
    idx.add("sprint", entry)

    assert len(idx.entries["sprint"]) == 1, (
        "misma clave (token,node_id,field) debe deduplicarse (dedup O(1))"
    )
    assert ("sprint", "node-1", "name") in idx._keys


def test_token_index_remove_node_limpia_keys():
    """remove_node() debe limpiar _keys para que el dedup siga consistente."""
    from fast_mcp.shared_kernel.cognitive_graph.models import IndexEntry, TokenIndex

    idx = TokenIndex()
    e1 = IndexEntry(node_id="node-1", field="name", snippet="Sprint")
    e2 = IndexEntry(node_id="node-2", field="name", snippet="Sprint")

    idx.add("sprint", e1)
    idx.add("sprint", e2)

    idx.remove_node("node-1")

    assert ("sprint", "node-1", "name") not in idx._keys
    # Re-añadir node-1 tras removerlo debe volver a indexar
    idx.add("sprint", e1)
    assert len(idx.entries["sprint"]) == 2
    assert ("sprint", "node-1", "name") in idx._keys


# ── 3. get_metadata_only: fingerprint sin load completo del CPG ─────


def test_read_fingerprint_metadata_only(tmp_path):
    """read_fingerprint() lee SOLO la tabla meta — SIN poblar el read model.

    Causa raíz del timeout -32001 restante: el watcher.scan() invoca
    read_fingerprint() en CADA chequeo de staleness, y antes hacía
    load() completo del CPG (json.loads + sha1(json.dumps) POR FILA:
    ~80K serializaciones con 15K docs + 24K edges) SOLO para leer un
    string — minutos de CPU dentro del event loop del server MCP.
    """
    from fast_mcp.features.code_analysis.infrastructure.cognitive_cpg_repo import (
        CognitiveCpgRepository,
    )

    repo = CognitiveCpgRepository(str(tmp_path), "demo")
    repo.save(
        {
            "project_id": "demo",
            "source_fingerprint": "fp-abc-123",
            "nodes": {"n1": {"name": "foo", "path": "a.py", "type": "function"}},
            "edges": [{"type": "CALLS", "from": "n1", "to": "n2"}],
            "stats": {"total_nodes": 1},
        }
    )

    # Instancia NUEVA (read model vacío) sobre el mismo data_dir: el
    # fingerprint DEBE leerse de la tabla meta sin cargar docs/edges.
    repo2 = CognitiveCpgRepository(str(tmp_path), "demo")
    assert repo2.read_fingerprint() == "fp-abc-123"

    idx = repo2._cg.get_index("cpg")  # noqa: SLF001 — contrato de test
    assert idx._docs == {}, (
        "get_metadata_only NO debe poblar el read model (docs)"
    )
    assert idx._saved_docs == {}, (
        "get_metadata_only NO debe calcular sha1 por fila (_saved_docs)"
    )

    # Y el read model sigue disponible lazy para usos reales:
    data = repo2.load("demo")
    assert "n1" in data.get("nodes", {})
    assert data.get("source_fingerprint") == "fp-abc-123"


def test_puerto_storage_backend_incluye_get_metadata_only():
    """El Protocol StorageBackend exige get_metadata_only en los backends.

    El puerto es @runtime_checkable: si un backend no implementa la
    primitiva, isinstance() falla en toda la app (composition, repos).
    """
    from fast_mcp.shared_kernel.cognitive_graph.ports import StorageBackend
    from fast_mcp.shared_kernel.cognitive_graph.sqlite_backend import SqliteBackend
    from fast_mcp.shared_kernel.cognitive_graph.file_backend import FileBackend

    for backend_cls in (SqliteBackend, FileBackend):
        assert isinstance(backend_cls, StorageBackend), (
            f"{backend_cls.__name__} no satisface el puerto StorageBackend"
        )
        assert hasattr(backend_cls, "get_metadata_only"), (
            f"{backend_cls.__name__} sin get_metadata_only (fail-fast del puerto)"
        )


# ── FIX F: el sync incremental guarda el nodo COMPLETO (con path) ────


def test_index_task_files_sincroniza_payload_completo_con_path(tmp_path, monkeypatch):
    """El sync incremental a la memoria manda los nodos completos.

    Bug medido 2026-11-16: `index_task_files` enviaba a `store_many` un resumen
    {name, type, file, line}. `file` no es la clave del CPG (`path`), así que
    el nodo quedaba sin path: el siguiente update no lo podía remover por path
    y `code_search` no lo encontraba (34 nodos huérfanos en backend).
    """
    from fast_mcp.features.code_analysis.application.build_service import (
        BuildGraphUseCase,
    )

    class _RepoEnMemoria:
        def __init__(self):
            self.data = {"nodes": {}, "edges": [], "stats": {}}

        def load(self, project_id):
            return self.data

        def save(self, data):
            self.data = data

        def get_stats(self, project_id):
            return {}

    class _CgFalso:
        def __init__(self):
            self.stored: dict[str, dict] = {}

        def store_many(self, index, docs):
            self.stored.update(docs)
            return {"stored": len(docs)}

        def delete(self, index, node_id):
            return {"deleted": True}

    (tmp_path / "b.py").write_text("def nueva():\n    return 1\n")
    cg = _CgFalso()
    uc = BuildGraphUseCase(
        project_id="demo",
        data_dir=str(tmp_path / "data"),
        code_root=str(tmp_path),
        repo_path=str(tmp_path),
        repo=_RepoEnMemoria(),
        graph=cg,
    )
    # El reporte de calidad no aporta al caso y toca el grafo real.
    monkeypatch.setattr(uc, "_persist_quality_report", lambda *a, **k: {})

    uc.index_task_files(["b.py"])

    assert cg.stored, "el incremental no sincronizó ningún nodo a la memoria"
    for node_id, doc in cg.stored.items():
        assert doc.get("path"), f"nodo {node_id} sincronizado sin 'path': {doc}"
        assert "file" not in doc, f"nodo {node_id} usa 'file' en vez de 'path'"


def test_update_fail_fast_sin_fallback_build(monkeypatch, tmp_path):
    """update() con watcher roto NO cae a build() completo (INV-016).

    El fallback silencioso disparaba minutos de CPU dentro del event loop
    del server MCP; el warning solo viajaba en un result que el cliente
    nunca veía (el request expiraba). Ahora: error estructurado accionable.
    """
    import fast_mcp.shared_kernel.project_support as ps
    from fast_mcp.features.code_analysis.application.build_service import (
        BuildGraphUseCase,
    )

    class _WatcherRoto:
        def __init__(self, *a, **k):
            raise RuntimeError("watcher simulado roto")

    monkeypatch.setattr(ps, "FileWatcher", _WatcherRoto)

    uc = BuildGraphUseCase(
        project_id="demo",
        data_dir=str(tmp_path / "data"),
        code_root=str(tmp_path),
        repo_path=str(tmp_path),
    )

    def _no_build(*a, **k):
        raise AssertionError("build() NO debe invocarse desde update() (INV-016)")

    monkeypatch.setattr(uc, "build", _no_build)

    result = uc.update()
    assert result["ok"] is False
    assert result["updated"] is False
    assert "watcher no disponible" in result["error"]
    assert "build_code_index" in result["error"], (
        "el error debe ser accionable: remitir al rebuild explícito"
    )


# ── 5. FIX B: hash del payload crudo — 0 re-writes sin cambios ──────


def test_sqlite_save_sin_rewrites_de_filas_sin_cambios(tmp_path):
    """Re-save de contenido idéntico = 0 writes (updated_at estable).

    FIX perf: el hash del load era sha1(json.dumps(doc)) POR FILA
    (~40K dumps de docs + ~24K de edges en un CPG mediano); ahora es
    sha1 del raw almacenado y el payload se escribe canónico (sort_keys).
    """
    import glob
    import json
    import sqlite3

    from fast_mcp.features.code_analysis.infrastructure.cognitive_cpg_repo import (
        CognitiveCpgRepository,
    )

    data_dir = str(tmp_path / "mem")
    nodes = {
        "n1": {"name": "foo", "path": "a.py", "type": "function"},
        "n2": {"name": "bar", "path": "b.py", "type": "function"},
    }
    edges = [{"type": "CALLS", "from": "n1", "to": "n2"}]

    # Timestamp determinista por _save (updated_at tiene granularidad de 1s:
    # sin esto, 3 saves en el mismo segundo son indistinguibles)
    import fast_mcp.shared_kernel.cognitive_graph.sqlite_backend as sb_mod

    counter = {"n": 0}

    def _fake_strftime(fmt, t=None):  # noqa: ARG001
        counter["n"] += 1
        return f"2026-09-04T00:00:{counter['n']:02d}Z"

    orig_strftime = sb_mod.time.strftime

    repo = CognitiveCpgRepository(data_dir, "demo")
    repo.save(
        {
            "project_id": "demo",
            "nodes": nodes,
            "edges": edges,
            "stats": {"total_nodes": 2},
        }
    )

    db_files = glob.glob(os.path.join(data_dir, "**", "*.sqlite3"), recursive=True)
    assert db_files, "la BD sqlite debe existir tras el save"
    conn = sqlite3.connect(f"file:{db_files[0]}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    def _updated_at(doc_id: str) -> str:
        row = conn.execute(
            "SELECT updated_at, data FROM docs WHERE `index`='cpg' AND id=?",
            (doc_id,),
        ).fetchone()
        return row["updated_at"]

    try:
        sb_mod.time.strftime = _fake_strftime  # save#1 → ts :01
        ts_n1_v1 = _updated_at("n1")

        # Payload canónico (sort_keys) — el contrato del FIX B
        row = conn.execute(
            "SELECT data FROM docs WHERE `index`='cpg' AND id='n1'"
        ).fetchone()
        assert json.dumps(json.loads(row["data"]), sort_keys=True) == row["data"]

        # Instancia fresca (load completo con hashes del raw) + save IDÉNTICO:
        # ningún doc cambió → 0 rewrites (updated_at estable)
        repo2 = CognitiveCpgRepository(data_dir, "demo")
        repo2.save(
            {
                "project_id": "demo",
                "nodes": nodes,
                "edges": edges,
                "stats": {"total_nodes": 2},
            }
        )  # save#2 → ts :02 (pero 0 rewrites → DB conserva :01)
        assert _updated_at("n1") == ts_n1_v1, (
            "el re-save sin cambios NO debe reescribir filas "
            "(el diff por hash del raw debe detectar igualdad)"
        )

        # Cambio SOLO n1 → n1 se reescribe, n2 NO (aislamiento del delta)
        nodes2 = dict(nodes)
        nodes2["n1"] = {"name": "foo2", "path": "a.py", "type": "function"}
        repo3 = CognitiveCpgRepository(data_dir, "demo")
        repo3.save(
            {
                "project_id": "demo",
                "nodes": nodes2,
                "edges": edges,
                "stats": {"total_nodes": 2},
            }
        )  # save#3 → ts :03
        ts_n1_v3 = _updated_at("n1")
        assert ts_n1_v3 != ts_n1_v1, "n1 cambió → debe reescribirse"
        assert _updated_at("n2") == ts_n1_v1, (
            "n2 NO cambió → no debe reescribirse (write amplification = 0)"
        )
    finally:
        sb_mod.time.strftime = orig_strftime
        conn.close()


# ── 6. FIX C: offload del event loop a worker thread ────────────────


def test_tools_offload_a_worker_thread_no_bloquean_event_loop():
    """register_domain_tool ejecuta los runners sync en worker thread.

    FIX -32001: el runner sync inline en el event loop bloqueaba TODAS
    las tools del server mientras una corría (evidencia: sample nativo
    con task_step → json encoder en el hilo principal). Ahora una tool
    lenta NO retrasa a las demás.
    """
    import asyncio
    import inspect
    import time as _time

    from mcp.server.fastmcp import FastMCP

    from fast_mcp.registration import register_domain_tool

    server = FastMCP("perf-offload")

    def slow_runner(**kw):
        _time.sleep(0.6)
        return {"slow": True}

    def fast_runner(**kw):
        return {"fast": True}

    register_domain_tool(server, "slow", slow_runner, "desc", {"type": "object"})
    register_domain_tool(server, "fast", fast_runner, "desc", {"type": "object"})

    async def _main():
        slow_fn = server._tool_manager.get_tool("slow").fn  # noqa: SLF001
        fast_fn = server._tool_manager.get_tool("fast").fn  # noqa: SLF001
        assert inspect.iscoroutinefunction(fast_fn), (
            "la tool registrada debe ser async (offload a thread)"
        )
        slow_task = asyncio.ensure_future(slow_fn())
        await asyncio.sleep(0.05)
        t0 = _time.monotonic()
        fast_res = await fast_fn()
        dt_fast = _time.monotonic() - t0
        slow_res = await slow_task
        return fast_res, slow_res, dt_fast

    fast_res, slow_res, dt_fast = asyncio.run(_main())
    assert fast_res == {"fast": True}
    assert slow_res == {"slow": True}
    assert dt_fast < 0.5, (
        f"la tool fast tardó {dt_fast:.2f}s — el event loop quedó bloqueado "
        "por el runner lento (offload a worker thread roto)"
    )


# ── 7. FIX: SqliteIndex._do_load nunca seteaba _loaded ──────────────


def test_sqlite_load_una_sola_vez_por_indice(tmp_path):
    """_do_load corre UNA vez: _loaded nunca se seteaba en sqlite.

    BUG preexistente grave: el base _load() re-ejecutaba _do_load en CADA
    acceso (search/get/save) → re-SELECT de TODAS las filas + JSON parse
    por operación (16K docs + 24K edges en el CPG real: segundos por cada
    llamada, dentro del event loop del server MCP).
    """
    from fast_mcp.features.code_analysis.infrastructure.cognitive_cpg_repo import (
        CognitiveCpgRepository,
    )

    repo = CognitiveCpgRepository(str(tmp_path / "mem"), "demo")
    repo.save(
        {
            "project_id": "demo",
            "nodes": {"n1": {"name": "foo", "path": "a.py", "type": "function"}},
            "edges": [],
        }
    )

    # Instancia fresca (read model vacío) — la PRIMERA _load debe cargar;
    # las siguientes NO (return temprano por _loaded=True).
    # F3 (ADR-WALKER-075 D4): el load es de dos niveles — light
    # (_ensure_light, una vez por _meta_loaded) + materialización de docs
    # (_materialize_docs, una vez por _loaded). El invariante es el mismo:
    # NINGUNA fase de carga corre por acceso; se cuentan ambas.
    repo2 = CognitiveCpgRepository(str(tmp_path / "mem"), "demo")
    idx = repo2._cg.get_index("cpg")  # noqa: SLF001 — contrato de test
    calls = {"light": 0, "materialize": 0}
    orig_light = idx._ensure_light
    orig_materialize = idx._materialize_docs

    def counting_light():
        # el guard (_meta_loaded) vive DENTRO de _ensure_light — el
        # invariante cuenta TRABAJO real (re-SELECT), no llamadas al guard
        if not getattr(idx, "_meta_loaded", False):
            calls["light"] += 1
        orig_light()

    def counting_materialize():
        if not getattr(idx, "_loaded", False):
            calls["materialize"] += 1
        orig_materialize()

    idx._ensure_light = counting_light  # noqa: SLF001 — sombra de instancia
    idx._materialize_docs = counting_materialize  # noqa: SLF001

    idx._load()  # noqa: SLF001
    idx._load()  # noqa: SLF001
    idx._load()  # noqa: SLF001

    assert calls["light"] == 1, (
        f"_load_light corrió {calls['light']} veces — cada acceso re-cargaba "
        "el estado ligero desde sqlite"
    )
    assert calls["materialize"] == 1, (
        f"_materialize_docs corrió {calls['materialize']} veces — cada acceso "
        "re-materializaba el read model completo (write amplification / "
        "bug _loaded jamás seteado)"
    )


def test_fulltext_vivo_tras_reload_sqlite(tmp_path):
    """El token index se reconstruye al cargar desde sqlite (FTS sordo).

    Antes: el fulltext quedaba mudo tras cada restart (0 hits con el CPG
    poblado) — el índice solo se llenaba en memoria durante writes del
    mismo proceso; al reabrir, nadie lo reconstruía.
    """
    from fast_mcp.features.code_analysis.infrastructure.cognitive_cpg_repo import (
        CognitiveCpgRepository,
    )

    repo = CognitiveCpgRepository(str(tmp_path / "mem"), "demo")
    repo.save(
        {
            "project_id": "demo",
            "nodes": {
                "n1": {"name": "voucher_service", "path": "a.ts", "type": "function"},
                "n2": {"name": "unrelated", "path": "b.ts", "type": "function"},
            },
            "edges": [],
        }
    )

    repo2 = CognitiveCpgRepository(str(tmp_path / "mem"), "demo")
    result = repo2._cg.search("cpg", {"text": "voucher"})  # noqa: SLF001
    hits = result.get("hits", {}).get("total", 0)
    assert hits >= 1, (
        f"fulltext mudo tras reload sqlite: {hits} hits para 'voucher' "
        "con docs poblados (token index no reconstruido en _do_load)"
    )

def test_ignore_rules_patron_anidado_matchea_por_path(tmp_path):
    """`lib/es6/` (patrón con slash) debe matchear el path, no el basename.

    Antes solo se comparaba por segmento (`es6` != `lib/es6`), así que el
    directorio generado por ReScript se indexaba igual (228 nodos medidos).
    """
    from fast_mcp.shared_kernel.project_support.watcher import IgnoreRules

    root = tmp_path / "proj"
    root.mkdir()
    (root / ".walkerignore").write_text("lib/es6/\ngraphify-out/\n")
    ignore = IgnoreRules(str(root))

    assert ignore.is_ignored("lib/es6/") is True
    assert ignore.is_ignored("lib/es6/features/x.res.js") is True
    assert ignore.is_ignored("graphify-out/graph.json") is True
    assert ignore.is_ignored("shared/x.ts") is False


def test_walk_poda_directorio_anidado_ignorado(tmp_path):
    from fast_mcp.features.code_analysis.application.build_service import (
        _walk_code_files,
    )
    from fast_mcp.shared_kernel.project_support.watcher import IgnoreRules

    root = tmp_path / "proj"
    (root / "lib" / "es6").mkdir(parents=True)
    (root / "lib" / "es6" / "x.js").write_text("1")
    (root / "src").mkdir()
    (root / "src" / "a.ts").write_text("1")
    (root / ".walkerignore").write_text("lib/es6/\n")

    files = _walk_code_files([str(root)], IgnoreRules(str(root)))
    rels = {os.path.relpath(f, str(root)) for f in files}
    assert "src/a.ts" in rels
    assert not any("lib/es6" in r for r in rels), "el generado no debe recorrerse"


def test_ast_grep_calls_usa_el_binario_del_adapter():
    """SSOT del binario: ast_grep_calls NO debe re-resolverlo por nombre.

    Tenía su propio `AST_GREP_BIN = os.environ.get(...) or "ast-grep"` (wrapper
    npm 0.1.0), así que `sg run --json` devolvía basura y TODA la extracción de
    CALLS fallaba con JSONDecodeError en todos los lenguajes, no solo TS.
    """
    from fast_mcp.features.code_analysis.infrastructure import (
        ast_grep_adapter,
        ast_grep_calls,
    )

    assert ast_grep_calls.AST_GREP_BIN is ast_grep_adapter.AST_GREP_BIN
    assert ast_grep_calls.AST_GREP_BIN == ast_grep_adapter.AST_GREP_BIN
