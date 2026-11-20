"""test_project_init_config_bdd.py — project.json desde evidencia (P1.4).

El schema de `project.json` define identidad, design, architecture, coding,
quality y dependencies, pero `prepare` lo creaba con design/coding/quality
vacíos y `build_project_config` no escribía nada ("no writes"): cada proyecto
nacía con una constitución vacía y el agente tenía que preguntar las
convenciones cada vez.

Ahora BUILD_PROJECT propone desde evidencia (test framework, formatter, linter,
package manager) y REVIEW aplica al manifest sin pisar lo curado.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers import project_init


def _repo(tmp_path: Path) -> str:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "project.json").write_text(
        json.dumps(
            {
                "project": {
                    "meta": {"project_id": "init-bdd"},
                    "identity": {"name": "init-bdd", "language": "", "framework": ""},
                    "location": {"repo_path": str(tmp_path)},
                    "design": {"patterns": [], "layer_rules": {},
                               "forbidden_dependencies": []},
                    "architecture": {"module_organization": ""},
                    "coding": {"naming_convention": "", "formatter": "",
                               "linter": "", "test_framework": ""},
                    "quality_preferences": {},
                    "team": {"owners": []},
                    "dependencies": {"internal_modules": [], "external_services": []},
                }
            }
        )
    )
    (tmp_path / "tests").mkdir()
    return str(tmp_path)


def _ctx(root: str) -> ExecutionContext:
    return ExecutionContext.create(chain_dir=root, task_id="")


def _manifest(root: str) -> dict:
    return json.loads((Path(root) / "data" / "project.json").read_text())["project"]


def test_build_propuesta_desde_evidencia_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n[tool.ruff]\nline-length = 100\n"
    )
    root = _repo(tmp_path)

    out = project_init.build_project_config(_ctx(root), {}).data

    assert out["config"]["language"] == "python"
    assert out["config"]["test_framework"] == "pytest"
    assert out["config"]["formatter"] == "ruff"
    assert out["config"]["linter"] == "ruff"
    patch = out["manifest_patch"]
    assert patch["identity"]["language"] == "python"
    assert patch["coding"]["test_framework"] == "pytest"
    # Lo que no se puede inferir (design/quality) no se propone.
    assert "design" not in patch and "quality_preferences" not in patch


def test_build_propuesta_javascript(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"vitest": "1", "prettier": "1", "eslint": "1"}})
    )
    root = _repo(tmp_path)

    out = project_init.build_project_config(_ctx(root), {}).data

    assert out["config"]["language"] == "javascript"
    assert out["config"]["test_framework"] == "vitest"
    assert out["config"]["formatter"] == "prettier"
    assert out["config"]["linter"] == "eslint"


def test_apply_completa_lo_vacio(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n[tool.ruff]\n")
    root = _repo(tmp_path)

    out = project_init.apply_project_config(_ctx(root), {"intent": "project_ready"}).data

    assert out["ok"] is True
    manifest = _manifest(root)
    assert manifest["identity"]["language"] == "python"
    assert manifest["coding"]["test_framework"] == "pytest"
    assert manifest["coding"]["formatter"] == "ruff"
    assert "identity.language" in out["applied"]
    # Idempotente: la segunda pasada no re-aplica nada.
    again = project_init.apply_project_config(_ctx(root), {"intent": "project_ready"}).data
    assert again["applied"] == []


def test_apply_no_pisa_lo_curado(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n[tool.ruff]\n")
    root = _repo(tmp_path)
    # El humano eligió otro linter: la detección no puede sobrescribirlo.
    path = Path(root) / "data" / "project.json"
    data = json.loads(path.read_text())
    data["project"]["coding"]["linter"] = "flake8"
    data["project"]["design"]["patterns"] = ["ports-and-adapters"]
    path.write_text(json.dumps(data))

    out = project_init.apply_project_config(_ctx(root), {"intent": "project_ready"}).data

    manifest = _manifest(root)
    assert manifest["coding"]["linter"] == "flake8"
    assert manifest["design"]["patterns"] == ["ports-and-adapters"]
    assert any("curado" in s for s in out["skipped"])


def test_apply_ignora_revise_specs(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    root = _repo(tmp_path)

    out = project_init.apply_project_config(_ctx(root), {"intent": "revise_specs"}).data

    assert out["applied"] == []
    assert "intent=revise_specs" in out["reason"]
    assert _manifest(root)["coding"]["test_framework"] == ""
