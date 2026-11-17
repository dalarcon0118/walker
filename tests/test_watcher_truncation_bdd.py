"""test_watcher_truncation_bdd.py — el sync incremental no puede perder archivos.

Bug medido 2026-11-17 en el propio repo de walker: se commiteó código nuevo en
`harness/runner.py` y el CPG siguió sirviendo la versión vieja (sin
`detect_project`, `eval_knowledge_nodes`, `summary_knowledge_nodes`), mientras
`status` reportaba `total_changes: 0 / cpg_status: up_to_date`.

Cadena causal:
1. `scan()` persiste `files_changed` truncado a MAX_CHANGED_FILES (500) en orden
   alfabético. Con más cambios, los que caen después del corte desaparecen.
2. `update()` consumía ese JSON truncado → indexaba solo los primeros 500.
3. `index_task_files` guardaba el fingerprint de TODO el árbol, así que
   `mark_indexed` veía `cpg_fp == fingerprint` y declaraba up_to_date, vaciando
   la cola.

Resultado: divergencia permanente y silenciosa. El fix expone la lista completa
en memoria (`last_changes_all`) y `update()` la consume.
"""

from __future__ import annotations

import fast_mcp.shared_kernel.project_support as project_support
import fast_mcp.shared_kernel.project_support.watcher as watcher_module
from fast_mcp.features.code_analysis.application.build_service import (
    BuildGraphUseCase,
)
from fast_mcp.shared_kernel.project_support.watcher import FileWatcher


def _repo_with_files(tmp_path, count: int):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    for i in range(count):
        (root / "src" / f"f{i:02d}.py").write_text("def f():\n    return 0\n")
    return root


def test_scan_expone_la_lista_completa_aunque_el_json_se_trunque(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher_module, "MAX_CHANGED_FILES", 10)
    root = _repo_with_files(tmp_path, 12)

    # data_dir FUERA del árbol escaneado: si el estado del watcher vive
    # dentro, cada scan se ve a sí mismo como cambio.
    watcher = FileWatcher(str(root), data_dir=str(tmp_path / "state"))
    watcher.scan()  # baseline: guarda el snapshot

    for i in range(12):
        (root / "src" / f"f{i:02d}.py").write_text("def f():\n    return 1\n")

    status = watcher.scan()

    assert status["total_changes"] == 12
    assert status["truncated"] is True, "el status debe decir que truncó"
    assert len(status["files_changed"]) == 10, "el JSON se sigue acotando"
    assert len(watcher.last_changes_all) == 12, "en memoria va la lista completa"
    assert "src/f11.py" in watcher.last_changes_all
    assert "src/f11.py" not in status["files_changed"], (
        "regresión: el último archivo alfabético caía fuera del corte"
    )


def test_update_consume_la_lista_completa_y_no_la_truncada(tmp_path, monkeypatch):
    """`update()` debe indexar los 12, no los 10 del JSON."""
    monkeypatch.setattr(watcher_module, "MAX_CHANGED_FILES", 10)
    files = [f"src/f{i:02d}.py" for i in range(12)]

    class FakeWatcher:
        def __init__(self, *args, **kwargs):
            self.last_changes_all = list(files)

        def scan(self):
            return {
                "files_changed": files[:10],
                "total_changes": len(files),
                "truncated": True,
                "cpg_status": "stale",
            }

        def mark_indexed(self, _index_type="cpg"):
            self.marked = True

    monkeypatch.setattr(project_support, "FileWatcher", FakeWatcher)

    class FakeRepo:
        def get_stats(self, project_id=""):
            return {"total_nodes": 100, "total_edges": 1000}

        def load(self, project_id=""):
            return {"nodes": {}, "edges": []}

    use_case = BuildGraphUseCase(
        "demo", str(tmp_path), str(tmp_path), repo=FakeRepo(), graph=object()
    )
    captured: dict = {}
    use_case.index_task_files = lambda changed, bug_proposals=False: (  # type: ignore[method-assign]
        captured.setdefault("files", list(changed)) or {"ok": True}
    )

    use_case.update()

    assert len(captured["files"]) == 12
    assert captured["files"][-1] == "src/f11.py"


# ── Guarda anti-erosión de aristas (2026-11-17) ───────────────────────────
# Los ids del scan son un nonce por corrida, así que el merge descarta las
# aristas entrantes a archivos re-escaneados y no las regenera. Medido en
# walker: 14.825 → 12.823 aristas tras un sync (CALLS 10.329 → 8.346).

def test_edge_regression_detecta_caida_significativa():
    from fast_mcp.features.code_analysis.application.build_service import (
        _edge_regression,
    )

    assert _edge_regression({"total_edges": 14825}, {"edges": [1] * 12823}) is True
    assert _edge_regression({"total_edges": 14825}, {"edges": [1] * 14800}) is False
    assert _edge_regression({"total_edges": 0}, {"edges": []}) is False


def test_update_rehace_completo_si_el_incremental_erosiona(tmp_path, monkeypatch):
    """Un merge que pierde aristas no puede quedar en disco en silencio."""
    monkeypatch.setattr(watcher_module, "MAX_CHANGED_FILES", 10)
    import fast_mcp.shared_kernel.project_support as project_support

    class FakeWatcher:
        last_changes_all = [f"src/f{i:02d}.py" for i in range(3)]

        def __init__(self, *args, **kwargs):
            pass

        def scan(self):
            return {"files_changed": self.last_changes_all, "total_changes": 3,
                    "cpg_status": "stale"}

        def mark_indexed(self, _index_type="cpg"):
            pass

    monkeypatch.setattr(project_support, "FileWatcher", FakeWatcher)

    use_case = BuildGraphUseCase(
        "demo", str(tmp_path), str(tmp_path),
        repo=type("R", (), {"get_stats": lambda self, pid="": {"total_nodes": 100, "total_edges": 1000}})(),
        graph=object(),
    )
    monkeypatch.setattr(
        use_case, "index_task_files",
        lambda changed, bug_proposals=False: {"nodes": {}, "edges": [1] * 900},
    )
    built: dict = {}
    monkeypatch.setattr(use_case, "build", lambda: built.setdefault("called", True) or {})

    use_case.update()

    assert built.get("called") is True, "debe rehacer el CPG completo"
