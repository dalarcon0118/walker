"""tests/test_create_adr_bdd.py — BDD tests for create_adr_from_spec side effect."""
from __future__ import annotations

from pathlib import Path

from features.task_execution.side_effects.create_adr import create_adr_from_spec
from tests.conftest import make_chain_dir


class FakeCtx:
    def __init__(self, chain_dir: str = "/tmp/test-sprint", session_id: str = "sess-1"):
        self.chain_dir = chain_dir
        self.session_id = session_id
        self.baggage = {}


def _setup_project(tmp_path: Path) -> str:
    """Crea un chain_dir mínimo (builder compartido en conftest)."""
    return make_chain_dir(tmp_path, "project", project_id="test")


def _seed_spring_and_spec(chain_dir: str):
    from features.delta_tracking import spring_service
    svc = spring_service(chain_dir=chain_dir)
    assert svc is not None
    spring_id = svc.get_spring_id(chain_dir)
    svc.create_spring(spring_id, goal="Test sprint", project_id="test")
    svc.add_spec(
        spring_id,
        objective="Create ADR for test",
        domain="test",
        spec_type="decision",
        spec_index=0,
        status="approved",
    )
    return svc, spring_id


class TestCreateAdrFromSpecBDD:
    def test_skips_when_approval_type_is_standard(self):
        ctx = FakeCtx()
        result = create_adr_from_spec(ctx, {"approval_type": "standard"})
        assert result["ok"] is True
        assert result.get("metadata", {}).get("skipped") is True

    def test_skips_when_approval_type_ref_resolves_to_standard(self):
        ctx = FakeCtx()
        result = create_adr_from_spec(ctx, {"approval_type": "NODE.TASK_SPEC.output.approval_type"})
        assert result["ok"] is True
        assert result.get("metadata", {}).get("skipped") is True

    def test_returns_error_when_no_chain_dir(self):
        ctx = FakeCtx(chain_dir="")
        result = create_adr_from_spec(ctx, {"approval_type": "decision"})
        assert result["ok"] is False
        assert "chain_dir" in result.get("metadata", {}).get("error", "")

    def test_returns_error_when_no_spec_found(self, tmp_path):
        chain_dir = _setup_project(tmp_path)
        ctx = FakeCtx(chain_dir=chain_dir)
        result = create_adr_from_spec(ctx, {"approval_type": "decision"})
        assert result["ok"] is False
        assert "no current spec" in result.get("metadata", {}).get("error", "")

    def test_creates_adr_and_links_to_spec(self, tmp_path):
        chain_dir = _setup_project(tmp_path)
        _seed_spring_and_spec(chain_dir)

        from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph
        ckg_initial = CodeKnowledgeGraph(chain_dir)
        initial_count = len(ckg_initial.query({"type": "decision"}))

        ctx = FakeCtx(chain_dir=chain_dir)
        result = create_adr_from_spec(ctx, {"approval_type": "decision"})

        assert result["ok"] is True
        adr_id = result.get("metadata", {}).get("adr_id")
        assert adr_id is not None
        assert result.get("metadata", {}).get("spec_id") == "SPEC-001"
        assert result.get("metadata", {}).get("related_decisions") == [adr_id]
        assert result.get("metadata", {}).get("generated_from") == "SPEC:SPEC-001"

        ckg_after = CodeKnowledgeGraph(chain_dir)
        final_count = len(ckg_after.query({"type": "decision"}))
        assert final_count == initial_count + 1

    def test_idempotent_does_not_duplicate_adr(self, tmp_path):
        chain_dir = _setup_project(tmp_path)
        _seed_spring_and_spec(chain_dir)

        from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph
        ckg_initial = CodeKnowledgeGraph(chain_dir)
        initial_count = len(ckg_initial.query({"type": "decision"}))

        ctx = FakeCtx(chain_dir=chain_dir)
        result1 = create_adr_from_spec(ctx, {"approval_type": "decision"})
        assert result1["ok"] is True
        adr_id_1 = result1.get("metadata", {}).get("adr_id")

        result2 = create_adr_from_spec(ctx, {"approval_type": "decision"})
        assert result2["ok"] is True
        adr_id_2 = result2.get("metadata", {}).get("adr_id")

        ckg_after = CodeKnowledgeGraph(chain_dir)
        count = len(ckg_after.query({"type": "decision"}))
        assert count == initial_count + 1
        assert adr_id_1 == adr_id_2
