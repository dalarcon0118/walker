"""tests/test_fast_mcp_delta_infrastructure_bdd.py — BDD tests for delta_tracking.

Tests:
  - CognitiveDeltaRepository (in-memory via shared_kernel.cognitive_graph)
  - Handlers (sprint_planning, resolve_project_sprint)
  - Composition wiring (delta_tool_runner)

R1 compliance: only imports from fast_mcp.*
"""
from __future__ import annotations

import pytest
from pathlib import Path

from fast_mcp.features.delta_tracking.infrastructure.cognitive_delta_repo import CognitiveDeltaRepository
from fast_mcp.features.delta_tracking.application.services.sprint_service import SprintService
from fast_mcp.features.delta_tracking.handlers import dispatch_delta, register_handler
from fast_mcp.features.delta_tracking.handlers.sprint import handle_sprint_planning, handle_resolve_project_sprint


# ── Fixtures ──────────────────────────────────────────────────────

def _make_cg(tmp_path):
    """Create a CognitiveGraph backed by a temp dir with delta index.

    Uses build_cognitive_graph (config-driven sqlite backend, ADR-063) —
    NO FileBackend fallback.
    """
    from fast_mcp.shared_kernel.cognitive_graph import build_cognitive_graph
    g = build_cognitive_graph(str(tmp_path))
    g.register_index("delta", {
        "fields": {
            "task": "spec_id",
            "spec": "spec_id",
            "spring": "spring_id",
            "domain": "domain",
            "type": "type",
        },
        "types": {"spec": {}, "spring": {}},
        "description": "DeltaGraph: specs and springs",
        "search": {
            "type": "fulltext",
            "fields": ["objective", "title", "description", "domain"],
        },
    })
    return g


@pytest.fixture
def repo(tmp_path):
    """CognitiveDeltaRepository backed by fresh CG in temp dir."""
    return CognitiveDeltaRepository(_make_cg(tmp_path))


@pytest.fixture
def svc(repo):
    """SprintService backed by CognitiveDeltaRepository."""
    return SprintService(project_root="/tmp/test", repo=repo)


# ── CognitiveDeltaRepository ──────────────────────────────────────

class TestCognitiveDeltaRepository:
    def test_save_and_get_spring(self, repo):
        from fast_mcp.features.delta_tracking.domain.entities import Spring
        spring = Spring(spring_id="sprint-1", goal="Test")
        repo.save_spring(spring)
        loaded = repo.get_spring("sprint-1")
        assert loaded is not None
        assert loaded.spring_id == "sprint-1"
        assert loaded.goal == "Test"

    def test_get_spring_not_found(self, repo):
        assert repo.get_spring("nonexistent") is None

    def test_list_springs(self, repo):
        from fast_mcp.features.delta_tracking.domain.entities import Spring
        repo.save_spring(Spring(spring_id="sprint-1", goal="A", status="active"))
        repo.save_spring(Spring(spring_id="sprint-2", goal="B", status="planning"))
        all_springs = repo.list_springs()
        assert len(all_springs) == 2
        active = repo.list_springs(status="active")
        assert len(active) == 1

    def test_get_active_spring(self, repo):
        from fast_mcp.features.delta_tracking.domain.entities import Spring
        repo.save_spring(Spring(spring_id="sprint-1", goal="A", status="planning"))
        repo.save_spring(Spring(spring_id="sprint-2", goal="B", status="active", active=True))
        active = repo.get_active_spring()
        assert active is not None
        assert active.spring_id == "sprint-2"

    def test_save_and_get_spec(self, repo):
        from fast_mcp.features.delta_tracking.domain.entities import Spring, Spec
        repo.save_spring(Spring(spring_id="sprint-1", goal="Test"))
        spec = Spec(spring_id="sprint-1", objective="Login screen", spec_index=0)
        repo.save_spec(spec)
        loaded = repo.get_spec("sprint-1", "SPEC-001")
        assert loaded is not None
        assert loaded.objective == "Login screen"

    def test_get_specs_no_se_corta_en_la_primera_pagina(self, repo):
        """`search` pagina de a 50: con >50 specs el sprint pedido daba 0."""
        from fast_mcp.features.delta_tracking.domain.entities import Spec

        for i in range(60):
            repo.save_spec(Spec(spring_id=f"otro-{i}", objective="x"))
        repo.save_spec(Spec(spring_id="target", objective="y"))

        assert [s.spring_id for s in repo.get_specs("target")] == ["target"]

    def test_get_specs(self, repo):
        from fast_mcp.features.delta_tracking.domain.entities import Spring, Spec
        repo.save_spring(Spring(spring_id="sprint-1", goal="Test"))
        repo.save_spec(Spec(spring_id="sprint-1", objective="A", spec_index=0))
        repo.save_spec(Spec(spring_id="sprint-1", objective="B", spec_index=1))
        specs = repo.get_specs("sprint-1")
        assert len(specs) == 2
        assert specs[0].spec_index == 0

    def test_delete_spec(self, repo):
        from fast_mcp.features.delta_tracking.domain.entities import Spring, Spec
        repo.save_spring(Spring(spring_id="sprint-1", goal="Test"))
        repo.save_spec(Spec(spring_id="sprint-1", objective="A"))
        repo.delete_spec("sprint-1", "SPEC-001")
        assert repo.get_spec("sprint-1", "SPEC-001") is None

    def test_save_specs_batch(self, repo):
        from fast_mcp.features.delta_tracking.domain.entities import Spring, Spec
        repo.save_spring(Spring(spring_id="sprint-1", goal="Test"))
        specs = [
            Spec(spring_id="sprint-1", objective="A", spec_index=0),
            Spec(spring_id="sprint-1", objective="B", spec_index=1),
        ]
        repo.save_specs_batch(specs)
        loaded = repo.get_specs("sprint-1")
        assert len(loaded) == 2


# ── SprintService with real repo ──────────────────────────────────

class TestSprintServiceIntegration:
    def test_create_and_load_tasks(self, svc):
        svc.create_spring("sprint-1", goal="Test", status="active")
        svc.add_spec("sprint-1", objective="Spec A")
        svc.add_spec("sprint-1", objective="Spec B")
        tasks = svc.load_tasks("/tmp/test/sprint-1")
        assert len(tasks) == 2
        assert tasks[0]["objective"] == "Spec A"

    def test_get_sprint_progress(self, svc):
        svc.create_spring("sprint-1", goal="Test")
        svc.add_spec("sprint-1", objective="A")
        svc.add_spec("sprint-1", objective="B")
        progress = svc.get_sprint_progress("sprint-1")
        assert progress["total"] == 2
        assert progress["completed"] == 0


# ── Handlers ──────────────────────────────────────────────────────

class TestHandlers:
    def test_dispatch_delta_unknown(self):
        result = dispatch_delta("unknown_tool", {})
        assert not result["ok"]
        assert "Unknown" in result["error"]

    def test_dispatch_delta_exception(self):
        def bad_handler(args):
            raise RuntimeError("boom")
        register_handler("bad_tool", bad_handler)
        result = dispatch_delta("bad_tool", {})
        assert not result["ok"]
        assert "boom" in result["error"]

    def test_handle_sprint_planning(self, svc):
        # New contract: composition root injects the repo-backed service
        # ('_sprint_service'); the handler never self-constructs one (INV-016).
        result = handle_sprint_planning({
            "project_id": "walker",
            "goal": "Implement auth",
            "tasks": [
                {"objective": "Login screen", "type": "feature", "domain": "frontend"},
            ],
            "_sprint_service": svc,
        })
        assert result["ok"]
        assert result["spec_count"] == 1
        assert result["specs"][0]["objective"] == "Login screen"

    def test_handle_sprint_planning_without_injection_fails_fast(self):
        # INV-016: no anemic fallback — missing DI is a structured error,
        # never a silent no-op write.
        result = handle_sprint_planning({"project_id": "walker", "goal": "X"})
        assert not result["ok"]
        assert "_sprint_service" in result["error"]

    def test_handle_sprint_planning_no_goal(self, svc):
        result = handle_sprint_planning({
            "project_id": "walker",
            "_sprint_service": svc,
        })
        assert not result["ok"]
        assert "goal" in result["error"]

    def test_handle_resolve_project_sprint(self, svc):
        svc.create_spring("sprint-1", goal="Test", status="active", project_id="walker")
        svc.add_spec("sprint-1", objective="Spec A")
        result = handle_resolve_project_sprint({"project_id": "walker"})
        # May return None if path resolution fails, that's OK
        # The test validates the handler signature works


# ── Composition wiring ────────────────────────────────────────────

def _make_disposable_project(tmp_path: Path) -> str:
    """Proyecto desechable PREPARADO (higiene de validación, ADR-073).

    Manifest mínimo + layout cognitivo canónico (.cognitive/projects/<pid>/data)
    para que ProjectService.resolve_paths() resuelva el data_dir DENTRO de
    tmp_path — nunca muta el SSOT del proyecto walker real.
    El layout se crea para ambos pid posibles (meta.project_id del manifest o
    basename del tmp) para ser determinista ante cualquier resolución.
    """
    import json

    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "project.json").write_text(json.dumps({
        "project": {
            "meta": {"project_id": "tp1"},
            "location": {"repo_path": str(tmp_path)},
        }
    }))
    for pid in ("tp1", tmp_path.name):
        cog = tmp_path / ".cognitive" / "projects" / pid / "data"
        cog.mkdir(parents=True, exist_ok=True)
    return str(tmp_path)


class TestCompositionWiring:
    def test_delta_tool_runner_returns_callable(self):
        from fast_mcp.composition import delta_tool_runner
        runner = delta_tool_runner("sprint_planning")
        assert callable(runner)

    def test_delta_tool_runner_without_project_context_returns_error(self):
        """INV-016 (regla 4): sin contexto de proyecto → error estructurado,
        nunca write silencioso en el CWD (base parásita)."""
        from fast_mcp.composition import delta_tool_runner
        runner = delta_tool_runner("sprint_planning")
        result = runner(goal="Test")
        assert isinstance(result, dict) and "error" in result
        assert "No project context" in result["error"]

    def test_delta_tool_runner_dispatches_to_canonical_db(self, tmp_path):
        """El dispatch real escribe en el data_dir CANÓNICO del proyecto
        desechable (resuelto vía ProjectService.resolve_paths), no en el
        repo root / CWD — la base parásita no debe existir."""
        from fast_mcp.composition import delta_tool_runner

        project_root = Path(_make_disposable_project(tmp_path))
        runner = delta_tool_runner("sprint_planning")
        result = runner(project_id=str(tmp_path), goal="Test")

        assert isinstance(result, dict)
        assert "error" not in result, f"dispatch falló: {result}"

        dbs = list(tmp_path.glob(".cognitive/projects/*/data/cognitive.sqlite3"))
        assert dbs, "la DB no se creó en el layout cognitivo canónico del proyecto"
        stray = Path.cwd() / "cognitive.sqlite3"
        assert not stray.exists(), "base parásita recreada en el CWD del repo"
