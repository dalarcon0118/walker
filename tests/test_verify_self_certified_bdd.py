"""test_verify_self_certified_bdd.py — los checks no se certifican con sus reglas (P3c).

`AGENT-EFFICIENCY.md` §8 / AE-INV-12: un cambio no puede probar que está bien
ejecutando reglas que ese mismo cambio tocó. No alcanza con "el test pasó": hay
que saber si el test es el mismo que existía en HEAD.

Modo `observe` por defecto (se marca `weak`, no se bloquea: corregir un test es
trabajo legítimo) y `strict=true` para cuando el gate tenga tasa de falsos
positivos medida.

Reglas de detección:
  * sólo cuentan `test_pass` y `command_exit` (los que EJECUTAN una regla);
  * sólo archivos que existen en HEAD (un test NUEVO no es una regla cambiada);
  * sin git o sin reglas se declara el motivo, no se asume verde.
"""
from __future__ import annotations

import subprocess

import pytest

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers import verify as verify_handlers


def _git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture()
def repo(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_ok():\n    assert True\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "base")
    return tmp_path


def _ctx(project_root):
    return ExecutionContext.create(chain_dir=str(project_root), task_id="T-1")


def _verify(project_root, predicates, **extra):
    return verify_handlers.verify_predicates(
        _ctx(project_root),
        {"project_root": str(project_root), "predicates": predicates, **extra},
    )


def test_regla_intacta_no_es_debil(repo):
    out = _verify(repo, [{"type": "command_exit", "command": "test -f tests/test_x.py"}])

    assert out.ok
    assert out.data["all_satisfied"] is True
    assert out.data["self_certified"] is False
    assert out.data["dirty_rules"] == []
    assert out.data["rules_check"] == "ok"
    assert "weak" not in out.data


def test_regla_modificada_marca_weak_pero_pasa_en_observe(repo):
    (repo / "tests" / "test_x.py").write_text("def test_ok():\n    assert 1 == 1\n")

    out = _verify(repo, [{"type": "command_exit", "command": "test -f tests/test_x.py"}])

    assert out.ok
    assert out.data["all_satisfied"] is True, "en observe el verde no se bloquea"
    assert out.data["self_certified"] is True
    assert out.data["dirty_rules"] == ["tests/test_x.py"]
    assert out.data["weak"] is True
    assert out.data["strict"] is False


def test_strict_convierte_la_debilidad_en_rojo(repo):
    (repo / "tests" / "test_x.py").write_text("def test_ok():\n    assert 1 == 1\n")

    out = _verify(
        repo,
        [{"type": "command_exit", "command": "test -f tests/test_x.py"}],
        strict=True,
    )

    assert out.ok
    assert out.data["all_satisfied"] is False
    assert out.data["strict"] is True
    assert out.data["failed_count"] == 1
    assert any(r["predicate"] == "checks:no_self_certified" for r in out.data["results"])


def test_un_test_nuevo_no_cuenta_como_regla_cambiada(repo):
    (repo / "tests" / "test_nuevo.py").write_text("def test_new():\n    assert True\n")

    out = _verify(repo, [{"type": "command_exit", "command": "test -f tests/test_nuevo.py"}])

    assert out.data["rules_check"] == "ok"
    assert out.data["self_certified"] is False


def test_predicados_que_no_ejecutan_reglas_no_se_miran(tmp_path):
    # `file_exists` / `grep` no ejecutan nada: no hay regla propia que cuidar.
    (tmp_path / "a.txt").write_text("x")

    out = _verify(tmp_path, [{"type": "file_exists", "path": "a.txt"}])

    assert out.data["all_satisfied"] is True
    assert out.data["rules_check"] == "no-rules"
    assert out.data["self_certified"] is False


def test_sin_git_se_declara_el_motivo(tmp_path):
    out = _verify(tmp_path, [{"type": "command_exit", "command": "test -f tests/test_x.py"}])

    assert out.ok
    assert out.data["rules_check"] in ("no-git", "git-error: OSError")
    assert out.data["self_certified"] is False
    assert out.data["dirty_rules"] == []


def test_extraccion_solo_de_predicados_ejecutables():
    rules = verify_handlers._rule_files(
        [
            {"type": "test_pass", "command": "tests/test_a.py"},
            {"type": "command_exit", "command": "python3 -m pytest tests/test_b.py -q"},
            {"type": "file_exists", "path": "tests/test_c.py"},
            {"type": "grep", "pattern": "tests/test_d.py"},
            "un string suelto",
        ]
    )
    assert rules == ["tests/test_a.py", "tests/test_b.py"]
