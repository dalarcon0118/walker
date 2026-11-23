"""tests/test_prepare_sgconfig_bdd.py — prepare siembra sgconfig para lenguajes custom.

Bug medido 2026-11-22: la siembra de `sgconfig.yml` (parser de PureScript para
ast-grep) vivía DESPUÉS del early return de idempotencia, así que un proyecto
ya preparado nunca la recibía. El CPG solo seguía funcionando por el fallback
al `data/sgconfig.yml` global del walker.

Contrato:
  - `seed_sgconfig` escribe rutas ABSOLUTAS al parser y al outline-rules.
  - Nunca pisa una config existente (puede estar curada a mano).
  - Sin parser descargado no escribe nada (best-effort, no inventa una config
    rota).
  - `prepare` siembra también en el path `already_exists`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fast_mcp.features.project.application import use_cases


def _walker_data(tmp_path: Path) -> Path:
    base = tmp_path / "walker-data"
    (base / "parsers").mkdir(parents=True)
    (base / "parsers" / "purescript.dylib").write_bytes(b"fake")
    (base / "outline_rules").mkdir()
    (base / "outline_rules" / "purescript.yml").write_text("rule: x\n")
    return base


def _ctx(tmp_path: Path):
    from fast_mcp import composition

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text("{}")
    return composition.build(root=fake_root).project


def _prepared_repo(tmp_path: Path, name: str = "demo-repo") -> Path:
    repo = tmp_path / name
    data = repo / ".cognitive" / "projects" / name / "data"
    data.mkdir(parents=True)
    (data / "project.json").write_text(json.dumps({"project": {"meta": {}}}))
    return repo


def test_seed_escribe_rutas_absolutas(tmp_path, monkeypatch):
    base = _walker_data(tmp_path)
    monkeypatch.setattr("fast_mcp.shared_kernel.sgconfig._walker_data_dir", lambda: base)
    dest = tmp_path / "proyecto" / "data"

    assert use_cases.seed_sgconfig(dest) is True

    text = (dest / "sgconfig.yml").read_text()
    assert "customLanguages:" in text
    assert f"libraryPath: {base / 'parsers' / 'purescript.dylib'}" in text
    assert f"outlineRules: {base / 'outline_rules' / 'purescript.yml'}" in text
    assert "extensions: [purs]" in text


def test_seed_no_pisa_lo_curado(tmp_path, monkeypatch):
    base = _walker_data(tmp_path)
    monkeypatch.setattr("fast_mcp.shared_kernel.sgconfig._walker_data_dir", lambda: base)
    dest = tmp_path / "proyecto" / "data"
    dest.mkdir(parents=True)
    (dest / "sgconfig.yml").write_text("# curado a mano\n")

    assert use_cases.seed_sgconfig(dest) is False
    assert (dest / "sgconfig.yml").read_text() == "# curado a mano\n"


def test_seed_sin_parser_no_escribe(tmp_path, monkeypatch):
    monkeypatch.setattr("fast_mcp.shared_kernel.sgconfig._walker_data_dir", lambda: tmp_path / "vacio")
    dest = tmp_path / "proyecto" / "data"

    assert use_cases.seed_sgconfig(dest) is False
    assert not (dest / "sgconfig.yml").exists()


def test_prepare_seed_en_proyecto_existente(tmp_path, monkeypatch):
    base = _walker_data(tmp_path)
    monkeypatch.setattr("fast_mcp.shared_kernel.sgconfig._walker_data_dir", lambda: base)
    repo = _prepared_repo(tmp_path)
    ctx = _ctx(tmp_path)

    result = use_cases.prepare_use_case(ctx, project_path=str(repo))

    assert result["type"] == "already_exists"
    assert result["sgconfig_seeded"] is True
    assert (repo / ".cognitive" / "projects" / repo.name / "data" / "sgconfig.yml").is_file()


def test_prepare_nuevo_devuelve_sgconfig_copiado(tmp_path, monkeypatch):
    base = _walker_data(tmp_path)
    monkeypatch.setattr("fast_mcp.shared_kernel.sgconfig._walker_data_dir", lambda: base)
    repo = tmp_path / "nuevo-repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='nuevo'\n")

    result = use_cases.prepare_use_case(_ctx(tmp_path), project_path=str(repo))

    assert result["type"] == "prepare_result"
    assert result["sgconfig_copied"] is True
    assert (repo / ".cognitive" / "projects" / "nuevo-repo" / "data" / "sgconfig.yml").is_file()
