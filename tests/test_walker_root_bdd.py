"""tests/test_walker_root_bdd.py — la raíz del walker NO depende del CWD.

Bug medido 2026-11-22: `walker_root()` subía buscando `config/config.json`;
ese archivo no existe en el repo (solo `config/hooks/`), así que caía a
`Path.cwd()`. Invocado desde un proyecto (p. ej. `client-chat`), el registro
se leía en `<proyecto>/data/projects.json` — inexistente — y `status` y
`list_projects` devolvían vacío. Un resultado plausible y equivocado.

Contrato:
  - `walker_root()` resuelve por marker del paquete, no por CWD.
  - `registry_path()` apunta al registro global aunque el CWD sea otro.
  - `WALKER_REPO_ROOT` (override documentado) gana sobre la detección.
"""

from __future__ import annotations

from fast_mcp.shared_kernel import paths


def _is_walker_root(path) -> bool:
    return (path / "fast_mcp" / "__init__.py").is_file()


def test_walker_root_no_depende_del_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    root = paths.walker_root()

    assert _is_walker_root(root)
    assert root != tmp_path.resolve()


def test_registry_path_es_global_desde_otro_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    registry = paths.registry_path()

    assert registry == paths.walker_root() / "data" / "projects.json"
    assert "data" in registry.parts and "projects.json" == registry.name


def test_env_override_gana(tmp_path, monkeypatch):
    monkeypatch.setenv("WALKER_REPO_ROOT", str(tmp_path))

    assert paths.walker_root() == tmp_path.resolve()


def test_env_override_vacio_se_ignora(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WALKER_REPO_ROOT", "   ")

    root = paths.walker_root()

    assert _is_walker_root(root)
    assert root != tmp_path.resolve()
