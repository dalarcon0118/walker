"""
Tests BDD (pytest-bdd) — Integración CPG <-> Project (regresión ADR-015).

Escenarios: bdd/features/cpg_project_integration.feature

Valida la cadena PÚBLICA ProjectService -> CPGBuilder, usando el manifest
(no atributos directos). REGLA DE ORO: cubrir la cadena pública.
"""

from __future__ import annotations

import json
import os

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/cpg_project_integration.feature")


@pytest.fixture
def pi_ctx() -> dict:
    """Estado compartido + limpieza del registro global."""
    yield {}


_cleanup_project = None


def _make_project(tmp: str) -> tuple[str, str]:
    """Crea un proyecto registrado real. Returns (repo_root, chain_dir)."""
    repo_root = os.path.join(tmp, "repo")
    src = os.path.join(repo_root, "src")
    os.makedirs(src)
    with open(os.path.join(src, "mod.py"), "w") as f:
        f.write("def add(a, b):\n    return a + b\n\nclass Service:\n    pass\n")

    cog_dir = os.path.join(repo_root, ".cognitive", "projects", "test-proj")
    os.makedirs(os.path.join(cog_dir, "data"))
    with open(os.path.join(cog_dir, "data", "project.json"), "w") as f:
        json.dump(
            {
                "project": {
                    "meta": {"project_id": "test-proj", "version": "1.0"},
                    "identity": {"name": "Test Proj", "language": "python"},
                    "location": {
                        "repo_path": repo_root,
                        "code_paths": ["src"],
                    },
                }
            },
            f,
        )
    chain_dir = os.path.join(cog_dir, "data", "spring", "sprint-1")
    os.makedirs(chain_dir)
    return repo_root, chain_dir


def _register(repo_root: str) -> None:
    from features.project import ProjectService

    ProjectService.create_project(
        project_id="test-proj",
        repo_path=repo_root,
        name="Test Proj",
        language="python",
    )


def _unregister() -> None:
    from libs.path_resolver import get_walker_root

    reg_path = get_walker_root() / "data" / "projects.json"
    if reg_path.exists():
        try:
            data = json.loads(reg_path.read_text())
            data.get("projects", {}).pop("test-proj", None)
            reg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception:
            pass


# ── Antecedentes ─────────────────────────────────────────────────


# Cada escenario crea su propio proyecto registrado en tmp.
@pytest.fixture
def pi_arr(tmp_path: str, pi_ctx: dict) -> dict:
    repo_root, chain_dir = _make_project(str(tmp_path))
    _register(repo_root)
    pi_ctx["repo_root"] = repo_root
    pi_ctx["chain_dir"] = chain_dir
    yield pi_ctx
    _unregister()


@given("un proyecto registrado con repo_path y chain_dir")
def _bdd_proyecto_registrado(pi_arr: dict):
    assert os.path.isdir(pi_arr["repo_root"])


# ── Escenario 1 ──────────────────────────────────────────────────


@when("se resuelve el proyecto desde chain_dir")
def _bdd_from_chain(pi_arr: dict):
    from features.project import ProjectService

    pi_arr["project"] = ProjectService.from_chain_dir(pi_arr["chain_dir"])


@then("el manifest expone el repo_path")
def _bdd_manifest_repo(pi_arr: dict):
    assert pi_arr["project"].manifest.repo_path == pi_arr["repo_root"]


@then("el manifest expone el nombre declarado")
def _bdd_manifest_name(pi_arr: dict):
    assert pi_arr["project"].manifest.name == "Test Proj"


@then("code_paths incluye los paths declarados")
def _bdd_manifest_paths(pi_arr: dict):
    assert "src" in pi_arr["project"].manifest.code_paths


# ── Escenario 2 ──────────────────────────────────────────────────


@when("se instancia un CPGBuilder con el repo_path")
def _bdd_cpg_builder_repo(pi_arr: dict):
    from features.code_analysis import CPGBuilder

    pi_arr["builder"] = CPGBuilder(pi_arr["repo_root"])


@then("repo_path del builder coincide con el repo real")
def _bdd_builder_repo(pi_arr: dict):
    assert pi_arr["builder"].repo_path == os.path.realpath(pi_arr["repo_root"])


@then("code_root del builder es un directorio existente")
def _bdd_builder_root(pi_arr: dict):
    assert os.path.isdir(pi_arr["builder"].code_root)


# ── Escenario 3 ──────────────────────────────────────────────────


@when("se ejecuta ProjectService → CPGBuilder → build")
def _bdd_cadena_completa(pi_arr: dict):
    from features.code_analysis import CPGBuilder
    from features.project import ProjectService

    paths = ProjectService.resolve_paths(pi_arr["chain_dir"])
    project_root = paths.repo_path or paths.project_root
    builder = CPGBuilder(project_root)
    pi_arr["result"] = builder.build()


@then("el build devuelve nodos (total_nodes > 0)")
def _bdd_nodos(pi_arr: dict):
    stats = pi_arr["result"].get("stats", pi_arr["result"])
    assert stats.get("total_nodes", 0) > 0, pi_arr["result"]
