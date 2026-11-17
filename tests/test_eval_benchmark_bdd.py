"""test_eval_benchmark_bdd.py — benchmark derivado + ablación.

La suite actual está saturada (8/8), así que no puede mostrar mejoras. Estas dos
piezas construyen el instrumento: (1) derivar tareas donde el agente falle desde
el historial git, (2) correr el mismo benchmark variando solo una capa de
walker.

Ninguna reemplaza la validación: `--check-tasks` tiene que confirmar que el test
FALLA en el base. Si pasa, la tarea no sirve.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from scripts import ablation
from scripts.derive_eval_tasks import (
    build_task,
    clean_subject,
    django_test_label,
    is_source_file,
    is_test_file,
    parse_git_log,
)

LOG = """__C__abc1234|fix(bet): la cancelación materializa la jugada
app/bet/views.py
tests/bet/test_cancel_materialize.py
__C__def5678|chore: bump deps
package.json
__C__999aaaa|test: solo test
tests/foo/test_bar.py
"""


def test_parse_git_log_agrupa_archivos():
    commits = parse_git_log(LOG)

    assert [c["sha"] for c in commits] == ["abc1234", "def5678", "999aaaa"]
    assert commits[0]["files"] == ["app/bet/views.py", "tests/bet/test_cancel_materialize.py"]


def test_test_y_fuente_se_distinguen():
    assert is_test_file("tests/bet/test_cancel.py")
    assert is_test_file("shared/utils/concurrency.test.ts")
    assert not is_test_file("app/bet/views.py")
    assert is_source_file("app/bet/views.py")
    assert not is_source_file("package.json")


def test_label_de_django():
    assert django_test_label("tests/bet/test_cancel.py") == "tests.bet.test_cancel"
    assert django_test_label("app/tests.py") == "app.tests"


def test_clean_subject_quita_prefijo_convencional():
    assert clean_subject("fix(bet): la jugada no se pierde") == "la jugada no se pierde"


def test_build_task_requiere_test_y_fuente():
    commits = parse_git_log(LOG)

    task = build_task(commits[0], kind="django")
    assert task is not None
    assert task["fix_commit"] == "abc1234"
    assert task["test"] == "tests.bet.test_cancel_materialize"
    assert task["test_files"] == ["tests/bet/test_cancel_materialize.py"]
    assert "no modifiques los tests" in task["prompt"].lower()

    assert build_task(commits[1]) is None, "chore sin tests"
    assert build_task(commits[2]) is None, "test-only no tiene fuente"


def test_build_task_descarta_chore_aunque_toque_tests():
    commit = {
        "sha": "aaa",
        "subject": "chore: limpia tests",
        "files": ["app/x.py", "tests/test_x.py"],
    }
    assert build_task(commit) is None


def test_build_task_descarta_commits_demasiado_amplios():
    commit = {
        "sha": "bbb",
        "subject": "fix: refactor masivo",
        "files": ["app/x.py", "tests/test_x.py"] + [f"app/m{i}.py" for i in range(30)],
    }
    assert build_task(commit) is None


def test_build_task_node_usa_test_cmd():
    commit = {
        "sha": "ccc",
        "subject": "fix(ui): el input no formatea la fecha",
        "files": ["src/date.js", "tests/date.test.ts"],
    }
    task = build_task(commit, kind="node")
    assert task["test_cmd"] == ["node", "--test"]
    assert "test" not in task


def test_ablation_build_cmd_por_brazo():
    base = ablation.build_cmd("/repo", "/t.json", "baseline", 2, "m/x", "/out")
    tools = ablation.build_cmd("/repo", "/t.json", "tools", 2, "m/x", "/out")
    full = ablation.build_cmd("/repo", "/t.json", "full", 2, "m/x", "/out")

    assert "--no-mcp" in base
    assert "--no-mcp" not in tools and "--no-mcp" not in full
    assert "--k" in tools and "2" in tools
    assert "--model" in full and "m/x" in full


def test_ablation_falla_con_brazo_desconocido(tmp_path, monkeypatch):
    tasks = tmp_path / "t.json"
    tasks.write_text("[]")
    monkeypatch.setattr("sys.argv", [
        "ablation.py", "--repo", str(tmp_path), "--tasks", str(tasks),
        "--arms", "baseline,nope",
    ])
    assert ablation.main() == 2


def test_toggles_de_prompt_desactivan_inyeccion(monkeypatch):
    from fast_mcp.features.workflow.application.prompts.manager import PromptManager

    manager = PromptManager(".")
    monkeypatch.setenv("WALKER_PROMPT_KNOWLEDGE", "0")
    monkeypatch.setenv("WALKER_PROMPT_SKILLS", "0")

    assert manager._knowledge_data({}) == {}  # noqa: SLF001
    assert manager._skills_data({"skill_intent": {"tags": ["x"]}}) == {}  # noqa: SLF001
