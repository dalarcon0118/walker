"""test_cpg_stale_fingerprint_bdd.py — `cpg_status: stale` eterno sin trabajo real.

Medido 2026-11-26: el status reportaba `stale` mientras `brief` devolvía los
símbolos del código actual. Causa: el fingerprint del árbol incluía 28 archivos
de ESTADO que walker escribe en cada operación (`data/metrics/tool_calls.jsonl`
cambia en cada tool call, `data/cognitive.sqlite3` en cada memoria,
`.workflow-state.json` en cada submit). El CPG guarda la firma del momento en
que indexó, así que nunca puede coincidir: stale para siempre, y el agente
aprende a ignorar la señal.

Contrato:
1. `is_walker_state` reconoce el estado volátil (workflow state, sqlite, jsonl,
   reportes) y no el código.
2. El fingerprint NO cambia cuando sólo cambia estado.
3. El fingerprint SÍ cambia cuando cambia una línea de código.
4. Lo que vive bajo `data_dir` nunca entra al fingerprint.
"""

from __future__ import annotations

import os

from fast_mcp.shared_kernel.project_support.watcher import FileWatcher, is_walker_state


def _watcher(root, data_dir=None):
    return FileWatcher(str(root), data_dir=str(data_dir) if data_dir else None)


def _write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def test_reconoce_el_estado_volatil():
    for rel in (
        ".workflow-state.json",
        ".workflow-state-SPEC-100.json",
        "data/cognitive.sqlite3",
        "data/cognitive.sqlite3-wal",
        "data/metrics/tool_calls.jsonl",
        "data/change_detected.json",
        "data/human_turns_report.json",
        ".file_snapshot.json",
    ):
        assert is_walker_state(rel), rel

    for rel in ("fast_mcp/cli.py", "data/sgconfig.yml", "docs/README.md"):
        assert not is_walker_state(rel), rel


def test_el_estado_no_cambia_el_fingerprint(tmp_path):
    _write(str(tmp_path / "src" / "app.py"), "def f(): return 1\n")
    w = _watcher(tmp_path, tmp_path / ".state")
    before = w.compute_fingerprint()

    _write(str(tmp_path / ".workflow-state.json"), '{"revision": 1}')
    _write(str(tmp_path / "data" / "metrics" / "tool_calls.jsonl"), "{}\n")
    _write(str(tmp_path / "data" / "cognitive.sqlite3"), "db")

    assert w.compute_fingerprint() == before


def test_el_codigo_si_cambia_el_fingerprint(tmp_path):
    _write(str(tmp_path / "src" / "app.py"), "def f(): return 1\n")
    w = _watcher(tmp_path, tmp_path / ".state")
    before = w.compute_fingerprint()

    _write(str(tmp_path / "src" / "app.py"), "def f(): return 2\n")

    assert w.compute_fingerprint() != before


def test_el_data_dir_nunca_entra_al_fingerprint(tmp_path):
    _write(str(tmp_path / "src" / "app.py"), "x = 1\n")
    data_dir = tmp_path / "state"
    _write(str(data_dir / "notes.txt"), "a")
    w = _watcher(tmp_path, data_dir)
    before = w.compute_fingerprint()

    _write(str(data_dir / "notes.txt"), "b")

    assert w.compute_fingerprint() == before
