"""test_workflow_src_bdd.py — el grafo del workflow es reproducible.

`data/workflow/` está gitignoreado. `workflow-src/` es la copia versionada que
`GraphRepository` siembra cuando falta, y `scripts/sync_workflow.py --check`
detecta drift entre ambos.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fast_mcp.features.workflow.infrastructure.graph_repository import _seed_defaults
from scripts.sync_workflow import diff_trees

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "workflow-src"


def test_workflow_src_esta_versionado_y_trae_el_artefacto():
    assert (SRC / "workflow.json").is_file()
    node = json.loads((SRC / "nodes" / "design.node.json").read_text())
    analyze = next(s for s in node["sub_states"] if s["key"] == "DESIGN_ANALYZE")
    assert analyze["input_schema"]["x-artifact"] == "design_brief"


def test_seed_copia_el_grafo_canonico_cuando_falta(tmp_path):
    shutil.copytree(SRC, tmp_path / "workflow-src")
    workflow_json = tmp_path / "data" / "workflow" / "workflow.json"

    _seed_defaults(str(workflow_json), root=tmp_path)

    assert workflow_json.is_file()
    assert (tmp_path / "data" / "workflow" / "nodes" / "start.node.json").is_file()


def test_no_siembra_un_path_arbitrario(tmp_path):
    shutil.copytree(SRC, tmp_path / "workflow-src")
    other = tmp_path / "otro" / "workflow.json"

    _seed_defaults(str(other), root=tmp_path)

    assert not other.exists(), "sembrar un path arbitrario ocultaría un error"


def test_diff_detecta_contenido_y_faltantes(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "nodes").mkdir(parents=True)
    (b / "nodes").mkdir(parents=True)
    (a / "workflow.json").write_text('{"x": 1}')
    (b / "workflow.json").write_text('{"x": 2}')
    (a / "nodes" / "n.json").write_text('{"k": 1}')

    diffs = diff_trees(a, b)

    assert any("workflow.json" in d for d in diffs)
    assert any("n.json" in d for d in diffs)


def test_diff_sin_cambios(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    shutil.copytree(SRC, a)
    shutil.copytree(SRC, b)

    assert diff_trees(a, b) == []
