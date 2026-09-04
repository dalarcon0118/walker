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