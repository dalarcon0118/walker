"""test_project_paths_legacy_bdd.py — resolución de paths por project_id.

Bug medido 2026-11-17 probando `task.after`: `capture` moría con
`NameError: name 'project_data_dir' is not defined` cuando el proyecto no tenía
el layout cognitivo preparado y caía al fallback `<repo>/data` (el import
estaba solo en otros métodos de la clase). Cualquier tool de task sobre un
proyecto recién registrado reventaba.
"""

from __future__ import annotations

import json
from pathlib import Path

from fast_mcp.shared_kernel.project_support import ProjectService


def _walker_root(tmp_path: Path, repo: Path) -> Path:
    walker = tmp_path / "walker"
    (walker / "data").mkdir(parents=True)
    (walker / "data" / "projects.json").write_text(
        json.dumps({"projects": {"demo": {"repo_path": str(repo)}}})
    )
    return walker


def test_fallback_legacy_data_cuando_no_hay_layout(tmp_path):
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    walker = _walker_root(tmp_path, repo)

    paths = ProjectService.resolve_from_project_id("demo", root=walker)

    assert paths.project_root == str(repo)
    assert paths.data_dir == str(repo / "data")


def test_usa_el_layout_cognitivo_cuando_existe(tmp_path):
    repo = tmp_path / "repo"
    layout = repo / ".cognitive" / "projects" / "demo" / "data"
    layout.mkdir(parents=True)
    walker = _walker_root(tmp_path, repo)

    paths = ProjectService.resolve_from_project_id("demo", root=walker)

    assert paths.data_dir == str(layout)
