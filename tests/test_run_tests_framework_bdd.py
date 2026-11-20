"""test_run_tests_framework_bdd.py — el runner sale del proyecto (P1.8).

Medido: `run_tests` ejecutaba `pytest -q` a mano y `verify_engine` buscaba el
manifiesto en `<repo>/data/project.json` (ruta que ya no existe: el canónico es
`<repo>/.cognitive/projects/<pid>/data/project.json`). Resultado: un proyecto
con `vitest` declarado corría `python3 -m pytest -k …`; un proyecto JS/Go no
podía certificar nunca y el gate de cierre quedaba en nudge infinito.

Regla: se ejecuta el framework DECLARADO. Sin declaración se falla con un
mensaje accionable; no se asume pytest (default silencioso = mentira).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers import implement as impl
from fast_mcp.features.tasks.infrastructure import verify_engine as ve
from fast_mcp.shared_kernel.project_support import ProjectService


def _repo(tmp_path: Path, framework: str | None) -> Path:
    repo = tmp_path / "demo"
    repo.mkdir()
    data = repo / ".cognitive" / "projects" / "demo" / "data"
    data.mkdir(parents=True)
    coding = {} if framework is None else {"test_framework": framework}
    (data / "project.json").write_text(
        json.dumps(
            {
                "project": {
                    "meta": {"project_id": "demo"},
                    "location": {"repo_path": str(repo)},
                    "identity": {
                        "name": "demo",
                        "language": "javascript",
                        "framework": "",
                    },
                    "coding": coding,
                }
            }
        )
    )
    return repo


def _paths(monkeypatch, repo: Path) -> None:
    data_dir = repo / ".cognitive" / "projects" / "demo" / "data"
    monkeypatch.setattr(
        ProjectService,
        "resolve_paths",
        staticmethod(
            lambda chain_dir: SimpleNamespace(
                data_dir=str(data_dir), project_root=str(repo)
            )
        ),
    )


class _Result:
    def __init__(self, rc: int, out: str = "") -> None:
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


# ── resolución del framework ───────────────────────────────────────


def test_lee_el_manifiesto_del_layout_canonico(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "vitest")
    _paths(monkeypatch, repo)

    assert ve._read_declared_framework(str(repo)) == "vitest"
    assert ve.resolve_test_framework(str(repo)) == "vitest"


def test_el_comando_respeta_el_framework_y_la_flag(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "vitest")
    _paths(monkeypatch, repo)

    assert ve.build_test_command(str(repo)) == ["npx", "vitest", "run"]
    assert ve.build_test_command(str(repo), name="login") == [
        "npx", "vitest", "run", "-t", "login",
    ]


def test_go_usa_su_runner_y_su_flag(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "go")
    _paths(monkeypatch, repo)

    assert ve.build_test_command(str(repo), name="TestLogin") == [
        "go", "test", "-run", "TestLogin",
    ]


def test_sin_declaracion_no_asume_pytest(tmp_path, monkeypatch):
    repo = _repo(tmp_path, None)
    _paths(monkeypatch, repo)

    with pytest.raises(ve.TestRunnerUnavailable) as exc:
        ve.resolve_test_framework(str(repo))
    assert "coding.test_framework" in str(exc.value)
    assert "pytest" in str(exc.value), "el mensaje debe decir que no se asume"


def test_framework_desconocido_falla_con_soportados(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "mystery-runner")
    _paths(monkeypatch, repo)

    with pytest.raises(ve.TestRunnerUnavailable) as exc:
        ve.build_test_command(str(repo))
    assert "mystery-runner" in str(exc.value)
    assert "pytest" in str(exc.value) and "jest" in str(exc.value)


# ── la tool run_tests ──────────────────────────────────────────────


def _ctx(repo: Path) -> ExecutionContext:
    return ExecutionContext.create(chain_dir=str(repo), session_id="s1")


def test_run_tests_usa_el_runner_declarado(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "vitest")
    _paths(monkeypatch, repo)
    seen: dict = {}

    def _run(argv, **kwargs):
        seen["argv"] = argv
        return _Result(0, "Tests  3 passed (3)")

    monkeypatch.setattr(impl.subprocess, "run", _run)

    out = impl.run_tests(_ctx(repo), {"task_id": "T-1"})

    assert out.ok, out.error
    assert seen["argv"] == ["npx", "vitest", "run"]
    assert out.data["runner"] == "npx vitest run"
    assert out.data["test_framework"] == "vitest"
    assert out.data["total"] == 3


def test_run_tests_sin_declaracion_falla_y_no_ejecuta(tmp_path, monkeypatch):
    repo = _repo(tmp_path, None)
    _paths(monkeypatch, repo)

    def _run(*a, **k):  # pragma: no cover — no debe llamarse
        raise AssertionError("no debe ejecutar ningún runner sin declaración")

    monkeypatch.setattr(impl.subprocess, "run", _run)

    out = impl.run_tests(_ctx(repo), {})

    assert out.ok is False
    assert "coding.test_framework" in out.error


def test_run_tests_go_verde_no_exige_contadores(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "go")
    _paths(monkeypatch, repo)
    monkeypatch.setattr(
        impl.subprocess, "run", lambda argv, **k: _Result(0, "ok  demo  0.01s")
    )

    out = impl.run_tests(_ctx(repo), {})

    assert out.ok, out.error
    assert out.data["test_framework"] == "go"
    assert out.data["total"] == 0  # go no imprime "N passed": no es un fallo


def test_run_tests_pytest_cero_tests_sigue_siendo_rojo(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "pytest")
    _paths(monkeypatch, repo)
    monkeypatch.setattr(
        impl.subprocess, "run", lambda argv, **k: _Result(0, "no tests ran in 0.01s")
    )

    out = impl.run_tests(_ctx(repo), {})

    assert out.ok is False
    assert "0 tests" in out.error


def test_run_tests_rojo_reporta_el_framework(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "jest")
    _paths(monkeypatch, repo)
    monkeypatch.setattr(
        impl.subprocess, "run", lambda argv, **k: _Result(1, "1 failed, 2 passed")
    )

    out = impl.run_tests(_ctx(repo), {})

    assert out.ok is False
    assert "SUITE ROJA (jest:" in out.error


# ── verify_engine: contexto ausente es fallo accionable ────────────


def test_test_pass_sin_declaracion_reporta_error(tmp_path, monkeypatch):
    repo = _repo(tmp_path, None)
    _paths(monkeypatch, repo)

    passed, info = ve.verify_predicate({"type": "test_pass", "command": "test_x"}, str(repo))

    assert passed is False
    assert "coding.test_framework" in info["error"]
