"""BDD: acciones REFINE / PROJECT_INIT / ADD_TASKS que el grafo declaraba sin handler."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from fast_mcp.features.tasks.domain.task_registry import TaskRegistry
from fast_mcp.features.tasks.handlers import add_tasks, project_init, refine


class FakeSprintService:
    def __init__(self):
        self.springs: dict[str, dict] = {}
        self.specs: list[dict] = []

    def get_spring(self, spring_id):
        return self.springs.get(spring_id)

    def create_spring(self, spring_id, goal="", status="planning", project_id="", **kwargs):
        self.springs[spring_id] = {
            "spring_id": spring_id,
            "goal": goal,
            "status": status,
        }
        return self.springs[spring_id]

    def add_spec(self, spring_id, objective, domain="", spec_type="task", files=None, status="specified", **kwargs):
        spec = {
            "spec_id": f"SPEC-{len(self.specs) + 1:03d}",
            "objective": objective,
            "files": files or [],
        }
        self.specs.append(spec)
        return spec

    def get_session(self, session_id):
        return {"spring_id": "SPR-TEST"}


def _ctx(tmp_path):
    return SimpleNamespace(chain_dir=str(tmp_path), task_id="", session_id="s1")


def test_registra_las_ocho_acciones():
    registry = TaskRegistry()

    refine.register(registry)
    project_init.register(registry)
    add_tasks.register(registry)

    for name in (
        "refine_diagnose",
        "route_to_target",
        "escalate_refine",
        "detect_project_context",
        "build_project_config",
        "initialize_knowledge_base",
        "create_first_sprint",
        "add_tasks",
    ):
        assert registry.has(name), name


def test_refine_diagnose_valida_target_y_reason(tmp_path):
    ok = refine.refine_diagnose(
        _ctx(tmp_path), {"target": "implement", "reason": "el gate no cubría verifyFailed"}
    )
    assert ok.ok is True
    assert ok.data["status"] == "diagnosed"

    assert refine.refine_diagnose(_ctx(tmp_path), {"target": "nope", "reason": "x" * 20}).ok is False
    assert refine.refine_diagnose(_ctx(tmp_path), {"target": "verify", "reason": "corto"}).ok is False


def test_detect_project_context_por_marcadores(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    out = project_init.detect_project_context(_ctx(tmp_path), {})

    assert out.ok is True
    assert out.data["language"] == "python"
    assert "pyproject.toml" in out.data["markers"]

    config = project_init.build_project_config(_ctx(tmp_path), {})
    assert config.data["config"]["language"] == "python"


def test_create_first_sprint_es_idempotente(tmp_path, monkeypatch):
    svc = FakeSprintService()
    monkeypatch.setattr("fast_mcp.composition.build_sprint_service", lambda root: svc)

    first = project_init.create_first_sprint(_ctx(tmp_path), {"spring_id": "SPR-INIT", "goal": "inicio"})
    again = project_init.create_first_sprint(_ctx(tmp_path), {"spring_id": "SPR-INIT"})

    assert first.ok is True and first.data["existing"] is False
    assert again.ok is True and again.data["existing"] is True
    assert svc.springs["SPR-INIT"]["status"] == "planning"


def test_add_tasks_agrega_y_no_duplica_con_lista_vacia(tmp_path, monkeypatch):
    svc = FakeSprintService()
    svc.create_spring("SPR-TEST", goal="g")
    monkeypatch.setattr("fast_mcp.composition.build_sprint_service", lambda root: svc)

    out = add_tasks.add_tasks(
        _ctx(tmp_path),
        {"spring_id": "SPR-TEST", "tasks": [{"objective": "a"}, {"objective": "b"}]},
    )
    empty = add_tasks.add_tasks(_ctx(tmp_path), {"spring_id": "SPR-TEST", "tasks": []})

    assert out.ok is True and out.data["added"] == 2
    assert len(svc.specs) == 2
    assert empty.data["added"] == 0
    assert len(svc.specs) == 2


def test_route_to_target_invalido_falla(tmp_path):
    out = refine.route_to_target(_ctx(tmp_path), {"target": "nope"})

    assert out.ok is False
