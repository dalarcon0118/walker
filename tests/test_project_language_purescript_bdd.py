"""tests/test_project_language_purescript_bdd.py — detección y refresco de identidad.

Bug medido 2026-11-22: `client-chat` quedó registrado con
`identity.language = "javascript"` aunque 237 archivos son `.purs` y solo el
FFI es JS. El detector solo miraba `package.json` y el path de idempotencia de
`prepare` nunca volvía a detectar, así que el manifiesto envejecía en silencio.

Contrato:
  - Un repo Spago (`spago.yaml`/`spago.dhall`) se detecta como `purescript`,
    aunque también tenga `package.json` (Metro/Expo).
  - `refresh_manifest_identity` corrige `identity` de un proyecto ya preparado
    sin tocar el resto del manifiesto.
  - Nunca escribe `unknown` ni pisa un valor curado con una detección vacía.
"""

from __future__ import annotations

import json
from pathlib import Path

from fast_mcp.features.project.application.use_cases import (
    refresh_manifest_identity,
)
from fast_mcp.features.project.infrastructure.detection import detect_project


def _repo(tmp_path, name: str = "demo"):
    repo = tmp_path / name
    repo.mkdir()
    return repo


def _manifest(repo, language: str, framework: str = "react-native") -> str:
    data_dir = repo / ".cognitive" / "projects" / repo.name / "data"
    data_dir.mkdir(parents=True)
    manifest = {
        "project": {
            "meta": {"project_id": repo.name, "version": "1.0",
                     "created_at": "2026-11-01T00:00:00+00:00",
                     "updated_at": "2026-11-01T00:00:00+00:00"},
            "identity": {"name": repo.name, "language": language,
                         "framework": framework},
            "location": {"repo_path": str(repo)},
            "coding": {"test_framework": "spago"},
        }
    }
    (data_dir / "project.json").write_text(json.dumps(manifest))
    return str(data_dir)


# ── detección ───────────────────────────────────────────────────────


def test_spago_gana_a_package_json(tmp_path):
    repo = _repo(tmp_path)
    (repo / "package.json").write_text(json.dumps({"dependencies": {"expo": "1"}}))
    (repo / "spago.yaml").write_text("package: {}\n")

    assert detect_project(str(repo))["language"] == "purescript"


def test_spago_dhall_tambien_detecta(tmp_path):
    repo = _repo(tmp_path)
    (repo / "spago.dhall").write_text("{ name = \"demo\" }\n")

    assert detect_project(str(repo))["language"] == "purescript"


def test_solo_package_json_sigue_siendo_javascript(tmp_path):
    repo = _repo(tmp_path)
    (repo / "package.json").write_text(json.dumps({"dependencies": {"expo": "1"}}))

    assert detect_project(str(repo))["language"] == "javascript"


def test_pyproject_sigue_ganando_a_package_json(tmp_path):
    repo = _repo(tmp_path)
    (repo / "package.json").write_text("{}")
    (repo / "pyproject.toml").write_text("[project]\nname='d'\n")

    assert detect_project(str(repo))["language"] == "python"


# ── refresco del manifiesto ya preparado ────────────────────────────


def test_refresca_javascript_a_purescript(tmp_path):
    repo = _repo(tmp_path)
    (repo / "package.json").write_text(json.dumps({"dependencies": {"expo": "1"}}))
    (repo / "spago.yaml").write_text("package: {}\n")
    data_dir = _manifest(repo, "javascript")

    changed = refresh_manifest_identity(Path(data_dir), str(repo))

    assert changed["language"] == {"from": "javascript", "to": "purescript"}
    manifest = json.loads((Path(data_dir) / "project.json").read_text())
    assert manifest["project"]["identity"]["language"] == "purescript"
    # lo curado no se toca
    assert manifest["project"]["coding"]["test_framework"] == "spago"


def test_sin_cambios_no_escribe(tmp_path):
    repo = _repo(tmp_path)
    (repo / "spago.yaml").write_text("package: {}\n")
    data_dir = _manifest(repo, "purescript", framework="unknown")

    changed = refresh_manifest_identity(Path(data_dir), str(repo))

    assert changed == {}


def test_deteccion_unknown_no_pisa_lo_curado(tmp_path):
    repo = _repo(tmp_path)  # sin marcadores → language unknown
    data_dir = _manifest(repo, "purescript")

    changed = refresh_manifest_identity(Path(data_dir), str(repo))

    assert changed == {}
    manifest = json.loads((Path(data_dir) / "project.json").read_text())
    assert manifest["project"]["identity"]["language"] == "purescript"


def test_sin_manifiesto_es_noop(tmp_path):
    repo = _repo(tmp_path)
    (repo / "spago.yaml").write_text("package: {}\n")

    assert refresh_manifest_identity(repo / "nope", str(repo)) == {}
