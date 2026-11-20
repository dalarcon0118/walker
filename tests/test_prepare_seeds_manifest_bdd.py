"""test_prepare_seeds_manifest_bdd.py — `prepare` siembra la constitución (P1.4b).

`prepare` creaba `project.json` con `coding`/`design` vacíos y después nada lo
completaba hasta el flujo del workflow; un proyecto nuevo nacía sin constitución
y el agente re-derivaba convenciones de la prosa. Ahora la detección de tooling
vive en `shared_kernel/project_tooling` y la usan `prepare` y BUILD_PROJECT.

Reglas: se propone solo lo que tiene evidencia; sin evidencia, campo vacío (no
"unknown"); `prepare` es idempotente y nunca pisa lo curado.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fast_mcp.shared_kernel.project_tooling import detect_tooling


def _ctx(tmp_path: Path):
    from fast_mcp import composition

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text("{}")
    return composition.build(root=fake_root).project


def _repo(tmp_path: Path, name: str = "demo-repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    return repo


# ── helper compartido ──────────────────────────────────────────────


def test_detect_tooling_python_desde_pyproject(tmp_path):
    repo = _repo(tmp_path)
    (repo / "pyproject.toml").write_text(
        "[project]\nname='d'\n\n[tool.pytest.ini_options]\naddopts='-q'\n\n"
        "[tool.ruff]\nline-length=88\n"
    )

    out = detect_tooling(repo, "python")

    assert out["test_framework"] == "pytest"
    assert out["linter"] == "ruff"
    assert out["formatter"] == "ruff"


def test_detect_tooling_javascript_desde_package_json(tmp_path):
    repo = _repo(tmp_path)
    (repo / "package.json").write_text(
        json.dumps({"devDependencies": {"vitest": "^1", "eslint": "^9",
                                        "prettier": "^3"}})
    )

    out = detect_tooling(repo, "javascript")

    assert out == {"test_framework": "vitest", "formatter": "prettier",
                   "linter": "eslint"}


def test_detect_tooling_sin_evidencia_no_inventa(tmp_path):
    """M-09: sin evidencia no hay clave; nunca un placeholder."""
    repo = _repo(tmp_path)
    (repo / "package.json").write_text("{}")

    assert detect_tooling(repo, "python") == {}
    assert detect_tooling(repo, "javascript") == {}


# ── prepare ────────────────────────────────────────────────────────


def test_prepare_siembra_coding_desde_evidencia(tmp_path):
    from fast_mcp.features.project.application import use_cases

    repo = _repo(tmp_path)
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (repo / "pytest.ini").write_text("[pytest]\n")

    result = use_cases.prepare_use_case(_ctx(tmp_path), project_path=str(repo))

    assert result["type"] == "prepare_result"
    manifest = json.loads(Path(result["project_json"]).read_text())["project"]
    assert manifest["identity"]["language"] == "python"
    assert manifest["coding"]["test_framework"] == "pytest"
    # Lo que no se puede inferir sigue vacío: no se inventa.
    assert manifest["coding"]["naming_convention"] == ""
    assert manifest["coding"]["linter"] == ""
    assert manifest["design"] == {
        "patterns": [], "layer_rules": {}, "forbidden_dependencies": []
    }


def test_prepare_sin_evidencia_deja_campos_vacios(tmp_path):
    from fast_mcp.features.project.application import use_cases

    repo = _repo(tmp_path)
    (repo / "go.mod").write_text("module demo\n")

    result = use_cases.prepare_use_case(_ctx(tmp_path), project_path=str(repo))

    manifest = json.loads(Path(result["project_json"]).read_text())["project"]
    assert manifest["identity"]["language"] == "go"
    assert manifest["coding"]["test_framework"] == ""
    assert manifest["coding"]["formatter"] == ""
    assert manifest["coding"]["linter"] == ""


def test_prepare_es_idempotente_y_no_pisa_lo_curado(tmp_path):
    from fast_mcp.features.project.application import use_cases

    repo = _repo(tmp_path)
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (repo / "pytest.ini").write_text("[pytest]\n")
    ctx = _ctx(tmp_path)

    first = use_cases.prepare_use_case(ctx, project_path=str(repo))
    path = Path(first["project_json"])
    data = json.loads(path.read_text())
    data["project"]["coding"]["test_framework"] = "unittest"  # curación manual
    path.write_text(json.dumps(data))

    again = use_cases.prepare_use_case(ctx, project_path=str(repo))

    assert again["type"] == "already_exists"
    after = json.loads(path.read_text())["project"]
    assert after["coding"]["test_framework"] == "unittest"


@pytest.mark.parametrize("lang,marker", [("python", "pyproject.toml"),
                                         ("javascript", "package.json")])
def test_prepare_y_build_project_coinciden(tmp_path, lang, marker):
    """`prepare` y BUILD_PROJECT comparten helper: no pueden divergir (R1)."""
    from fast_mcp.features.project.application import use_cases
    from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
    from fast_mcp.features.tasks.handlers import project_init

    repo = _repo(tmp_path)
    if lang == "python":
        (repo / marker).write_text("[project]\nname='d'\n")
        (repo / "pytest.ini").write_text("[pytest]\n")
    else:
        (repo / marker).write_text(
            json.dumps({"devDependencies": {"jest": "^29", "eslint": "^9"}})
        )

    prepared = use_cases.prepare_use_case(_ctx(tmp_path), project_path=str(repo))
    seeded = json.loads(Path(prepared["project_json"]).read_text())["project"]["coding"]

    proposal = project_init.build_project_config(
        ExecutionContext.create(chain_dir=str(repo)), {}
    ).data["config"]

    for key in ("test_framework", "formatter", "linter"):
        assert seeded[key] == proposal[key]
