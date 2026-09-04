"""Tests del modelo ProjectManager (partes 2-4 del workstream).

Cubre:
  - PARTE 2a: ProjectService.get_repo_path(project_id) — basado en registro
    (projects.json), fail-fast INV-016 con ValueError accionable.
  - PARTE 2b: config del layout per-project (compose_project_layout /
    project.cognitive_dir / project.data_dir / project.ckg_dir) compuestas
    sobre el repo path.
  - PARTE 2c: resolve_from_project_id(project_id) — resuelve todas las rutas
    por registro, y resolve_paths(chain_dir) que reconoce un project_id.
"""

from __future__ import annotations

import json

import pytest


def _compose_build(tmp_path, config: dict | None = None):
    """Construye un walker-root de test con config opcional."""
    from fast_mcp import composition

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text(json.dumps(config or {}))
    return fake_root, composition.build(root=fake_root).project


def _register(fake_root, pid, repo_path):
    reg = fake_root / "data" / "projects.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    payload = {"projects": {pid: {"project_id": pid, "repo_path": repo_path}}}
    reg.write_text(json.dumps(payload))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Aísla el entorno para no resolver la raíz real del repo walker."""
    monkeypatch.delenv("WALKER_WORKFLOW_MANIFEST", raising=False)


# ── PARTE 2a: get_repo_path ──────────────────────────────────────────


def test_get_repo_path_resuelve_desde_registro(tmp_path, _clean_env):
    from fast_mcp.shared_kernel.project_support import ProjectService

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text("{}")
    repo = tmp_path / "mi-app"
    repo.mkdir()
    _register(fake_root, "mi-app", str(repo))

    assert ProjectService.get_repo_path("mi-app", root=fake_root) == str(repo.resolve())


def test_get_repo_path_fail_fast_no_registrado(tmp_path, _clean_env):
    from fast_mcp.shared_kernel.project_support import ProjectService

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text("{}")

    with pytest.raises(ValueError) as exc:
        ProjectService.get_repo_path("fantasma")
    assert "no registrado" in str(exc.value)
    assert "walker prepare" in str(exc.value)


def test_get_repo_path_fail_fast_sin_registro(tmp_path, _clean_env, monkeypatch):
    from fast_mcp.shared_kernel.project_support import ProjectService

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text("{}")

    monkeypatch.setattr(
        "fast_mcp.shared_kernel.paths.walker_root", lambda: fake_root
    )
    with pytest.raises(ValueError) as exc:
        ProjectService.get_repo_path("fantasma")
    assert "no encontrado" in str(exc.value)


# ── PARTE 2b: config del layout per-project ──────────────────────────


def test_compose_project_layout_por_config(tmp_path, _clean_env, monkeypatch):
    """compose_project_layout usa project.cognitive_dir/data_dir de config."""
    from pathlib import Path

    from fast_mcp.shared_kernel.paths import (
        compose_project_layout,
        project_cognitive_dir,
        project_data_dir,
    )

    monkeypatch.setattr(
        "fast_mcp.shared_kernel.paths._read_config_file",
        lambda: {"project": {"cognitive_dir": ".ctx", "data_dir": "mem"}},
    )

    assert project_cognitive_dir() == ".ctx"
    assert project_data_dir() == "mem"

    layout = compose_project_layout("/repo/mi-app", "mi-app")
    assert layout == Path("/repo/mi-app") / ".ctx" / "projects" / "mi-app" / "mem"


def test_compose_project_layout_defaults(tmp_path, _clean_env, monkeypatch):
    """Sin config project.*, los defaults son .cognitive/projects/<pid>/data."""
    from pathlib import Path

    from fast_mcp.shared_kernel.paths import compose_project_layout

    monkeypatch.setattr("fast_mcp.shared_kernel.paths._read_config_file", lambda: {})
    layout = compose_project_layout("/repo/mi-app", "mi-app")
    assert layout == Path("/repo/mi-app") / ".cognitive" / "projects" / "mi-app" / "data"


# ── PARTE 2c: resolve_from_project_id / resolve_paths ────────────────


def test_resolve_from_project_id_por_registro(tmp_path, _clean_env):
    from fast_mcp import composition
    from fast_mcp.features.project.application import use_cases
    from fast_mcp.shared_kernel.project_support import ProjectService

    fake_root, ctx = _compose_build(tmp_path)
    repo = tmp_path / "demo-repo"
    use_cases.prepare_use_case(ctx, project_path=str(repo), args={"language": "python"})
    _register(fake_root, "demo-repo", str(repo))

    paths = ProjectService.resolve_from_project_id("demo-repo", root=fake_root)
    assert paths.project_id == "demo-repo"
    assert paths.project_root == str(repo)
    cog_data = str(
        repo / ".cognitive" / "projects" / "demo-repo" / "data"
    )
    assert paths.data_dir == cog_data
    assert paths.project_json_path == f"{cog_data}/project.json"


def test_resolve_from_project_id_fail_fast_no_registrado(tmp_path, _clean_env):
    """resolve_from_project_id lanza fail-fast si el id no está en el registro."""
    from fast_mcp.shared_kernel.project_support import ProjectService

    fake_root, _ctx = _compose_build(tmp_path)
    reg = fake_root / "data" / "projects.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps({"projects": {}}))
    with pytest.raises(ValueError) as exc:
        ProjectService.resolve_from_project_id("fantasma", root=fake_root)
    assert "no registrado" in str(exc.value)


def test_resolve_paths_reconoce_project_id_registrado(tmp_path, _clean_env, monkeypatch):
    """resolve_paths(chain_dir) que recibe un project_id registrado → por id."""
    from fast_mcp import composition
    from fast_mcp.features.project.application import use_cases
    from fast_mcp.shared_kernel.project_support import ProjectService

    fake_root, ctx = _compose_build(tmp_path)
    repo = tmp_path / "demo-repo"
    use_cases.prepare_use_case(ctx, project_path=str(repo), args={"language": "python"})
    _register(fake_root, "demo-repo", str(repo))
    monkeypatch.setattr("fast_mcp.shared_kernel.paths.walker_root", lambda: fake_root)

    paths = ProjectService.resolve_paths("demo-repo")
    assert paths.project_id == "demo-repo"
    assert paths.project_root == str(repo)


def test_resolve_paths_chain_dir_legacy_backward_compat(tmp_path, _clean_env):
    """resolve_paths por path/layout sigue resolviendo igual (backward-compat)."""
    from fast_mcp import composition
    from fast_mcp.features.project.application import use_cases
    from fast_mcp.shared_kernel.project_support import ProjectService

    fake_root, ctx = _compose_build(tmp_path)
    repo = tmp_path / "demo-repo"
    use_cases.prepare_use_case(ctx, project_path=str(repo), args={"language": "python"})

    paths = ProjectService.resolve_paths(str(repo))
    cog_data = str(repo / ".cognitive" / "projects" / "demo-repo" / "data")
    assert paths.data_dir == cog_data
    assert paths.project_id == "demo-repo"
