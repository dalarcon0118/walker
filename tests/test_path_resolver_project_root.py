"""
Tests de regresión para resolve_project_root (app.path_resolver).

Cubre el layout cognitivo nuevo:
  <repo>/.cognitive/projects/<pid>/data/project.json

que permite resolver proyectos self-hosted (el propio walker: su manifest
NO está en la raíz del repo sino bajo .cognitive/) y sprints dentro del
dir cognitivo. También cubre el layout legacy y el bug del "double data/".
"""

from __future__ import annotations

import json
import os

import pytest

from app.path_resolver import resolve_project_root


def _write_project_json(cog_project_dir: str, repo_root: str, pid: str) -> None:
    """Escribe el manifest en <repo>/.cognitive/projects/<pid>/data/project.json."""
    data_dir = os.path.join(cog_project_dir, "data")
    os.makedirs(data_dir)
    with open(os.path.join(data_dir, "project.json"), "w") as f:
        json.dump(
            {
                "project": {
                    "meta": {"project_id": pid, "version": "1.0"},
                    "identity": {"name": "Proj", "language": "python"},
                    "location": {"repo_path": repo_root},
                }
            },
            f,
        )


@pytest.fixture
def cognitive_project(tmp_path) -> str:
    """Crea un proyecto con layout cognitivo. Returns repo_root."""
    repo = str(tmp_path / "repo")
    src = os.path.join(repo, "src")
    os.makedirs(src)
    cog = os.path.join(repo, ".cognitive", "projects", "self-hosted")
    _write_project_json(cog, repo, "self-hosted")
    return repo


def test_resolve_from_repo_root_cognitive_layout(cognitive_project: str) -> None:
    """La raíz del repo resuelve al project root cognitivo (self-hosted)."""
    proj = resolve_project_root(cognitive_project)
    assert proj == os.path.join(
        cognitive_project, ".cognitive", "projects", "self-hosted"
    )


def test_resolve_from_sprint_inside_cognitive_dir(cognitive_project: str) -> None:
    """Un sprint dentro del dir cognitivo sigue resolviendo (no solo la raíz)."""
    sprint = os.path.join(
        cognitive_project,
        ".cognitive",
        "projects",
        "self-hosted",
        "data",
        "spring",
        "sprint-1",
    )
    os.makedirs(sprint)
    proj = resolve_project_root(sprint)
    assert proj == os.path.join(
        cognitive_project, ".cognitive", "projects", "self-hosted"
    )


def test_resolve_deep_chain_dir_inside_cognitive(cognitive_project: str) -> None:
    """chain_dir profundo dentro del layout cognitivo sube hasta el project root."""
    deep = os.path.join(
        cognitive_project,
        ".cognitive",
        "projects",
        "self-hosted",
        "data",
        "traces",
        "s001.json.gz",
    )
    proj = resolve_project_root(deep)
    assert proj == os.path.join(
        cognitive_project, ".cognitive", "projects", "self-hosted"
    )


def test_resolve_legacy_data_project_json(tmp_path: str) -> None:
    """Layout legacy: <repo>/data/project.json sigue ganando (canónico)."""
    repo = str(tmp_path / "legacy")
    data_dir = os.path.join(repo, "data")
    os.makedirs(data_dir)
    with open(os.path.join(data_dir, "project.json"), "w") as f:
        json.dump({"project": {"meta": {"project_id": "legacy"}}}, f)
    assert resolve_project_root(repo) == repo


def test_resolve_legacy_direct_project_json(tmp_path: str) -> None:
    """Layout legacy directo: <repo>/project.json en la raíz."""
    repo = str(tmp_path / "legacy-direct")
    os.makedirs(repo)
    with open(os.path.join(repo, "project.json"), "w") as f:
        json.dump({"project": {"meta": {"project_id": "legacy-direct"}}}, f)
    assert resolve_project_root(repo) == repo


def test_resolve_double_data_bug_protection(tmp_path: str) -> None:
    """Bug del 'double data/': chain_dir dentro de .../data/spring/xxx/ no

    debe identificar un dir llamado 'data' como project root.
    """
    repo = str(tmp_path / "double-data")
    data_dir = os.path.join(repo, "data")
    os.makedirs(data_dir)
    with open(os.path.join(data_dir, "project.json"), "w") as f:
        json.dump({"project": {"meta": {"project_id": "dd"}}}, f)

    spring = os.path.join(data_dir, "spring", "s1")
    os.makedirs(spring)
    # chain_dir = self.do proj; el candidato 'data' debe retornar su padre
    assert resolve_project_root(data_dir) == repo
    # cadena profunda dentro de data/ también sube hasta el repo
    assert resolve_project_root(spring) == repo


def test_resolve_raises_when_no_manifest(tmp_path: str) -> None:
    """Sin manifest en la jerarquía -> FileNotFoundError."""
    empty = str(tmp_path / "empty")
    os.makedirs(empty)
    with pytest.raises(FileNotFoundError):
        resolve_project_root(empty)