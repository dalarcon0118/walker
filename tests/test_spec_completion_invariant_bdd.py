"""tests/test_spec_completion_invariant_bdd.py — BDD tests for spec completion invariant."""
from __future__ import annotations

import pytest

from fast_mcp.features.delta_tracking.domain.entities import Spec
from fast_mcp.features.delta_tracking.domain.spec_service import (
    SpecStatusUpdater,
    SpecService,
    SpecValidator,
)
from fast_mcp.features.delta_tracking.application.services.sprint_service import SprintService
from fast_mcp.features.delta_tracking.application.ports.delta_repository import DeltaRepository


class InMemoryDeltaRepo(DeltaRepository):
    def __init__(self):
        self._springs = {}
        self._specs = {}

    def get_spring(self, spring_id):
        return self._springs.get(spring_id)

    def save_spring(self, spring):
        self._springs[spring.spring_id] = spring

    def list_springs(self, status=""):
        out = list(self._springs.values())
        if status:
            out = [s for s in out if s.status == status]
        return out

    def get_active_spring(self):
        for s in self._springs.values():
            if s.status == "active" and s.active:
                return s
        return None

    def get_specs(self, spring_id, status=""):
        specs = [s for s in self._specs.values() if s.spring_id == spring_id]
        if status:
            specs = [s for s in specs if s.status == status]
        return sorted(specs, key=lambda s: s.spec_index)

    def get_spec(self, spring_id, spec_id):
        for s in self._specs.values():
            if s.spring_id == spring_id and s.spec_id == spec_id:
                return s
        return None

    def save_spec(self, spec):
        self._specs[spec.doc_id] = spec

    def save_specs_batch(self, specs):
        for s in specs:
            self._specs[s.doc_id] = s

    def delete_spec(self, spring_id, spec_id):
        self._specs.pop(f"{spring_id}:{spec_id}", None)

    def add_edge(self, from_id, to_id, edge_type):
        pass


def _artifacts():
    return {
        "completed_at": "2026-01-01T00:00:00+00:00",
        "commit_hash": "abc123",
        "review_verdict": "approved",
        "evidence": ["test passed"],
        # Invariante de cierre: el autor no firma su propio cierre, hace falta
        # una revisión independiente (`review_missing` en spec_service.py).
        "reviewed_by": "revisor-independiente",
    }


# (a)
def test_completed_without_artifacts_fails_and_names_fields():
    spec = {"status": "in_review"}
    with pytest.raises(ValueError) as exc:
        SpecStatusUpdater.update(spec, "completed")
    msg = str(exc.value)
    for field in ("completed_at", "commit_hash", "review_verdict", "evidence"):
        assert field in msg


# (b)
def test_completed_with_four_artifacts_passes():
    spec = {"status": "in_review", **_artifacts()}
    updated = SpecStatusUpdater.update(spec, "completed")
    assert updated["status"] == "completed"


# (c)
def test_update_spec_raw_blocks_closing_without_artifacts():
    repo = InMemoryDeltaRepo()
    svc = SprintService(project_root="/tmp", repo=repo)
    svc.create_spring("sprint-1", goal="Test")
    svc.add_spec("sprint-1", objective="Spec A")
    with pytest.raises(ValueError):
        svc.update_spec("sprint-1", "SPEC-001", {"status": "completed"})


def test_merge_blocks_closing_without_artifacts():
    spec = Spec(spring_id="sprint-1", objective="X", status="in_review")
    with pytest.raises(ValueError):
        SpecService.merge(spec, {"status": "completed"})


# (d)
def test_reopen_spec_returns_specified_and_allows_reclosing():
    repo = InMemoryDeltaRepo()
    svc = SprintService(project_root="/tmp", repo=repo)
    svc.create_spring("sprint-1", goal="Test")
    svc.add_spec("sprint-1", objective="Spec A")
    svc.update_spec("sprint-1", "SPEC-001", _artifacts())
    svc.update_spec_status("sprint-1", "SPEC-001", "completed")

    reopened = svc.reopen_spec("sprint-1", "SPEC-001", "fix tests")
    assert reopened is not None
    assert reopened["status"] == "specified"
    assert reopened["reopen_reason"] == "fix tests"
    assert reopened.get("reopened_at")

    # re-close with artifacts (still present)
    closed = svc.update_spec_status("sprint-1", "SPEC-001", "completed")
    assert closed["status"] == "completed"


def test_validator_names_completion_fields():
    spec = {"spec_id": "SPEC-1", "objective": "x", "files": ["a.py"], "status": "completed"}
    errors = SpecValidator.validate_spec(spec)
    assert any("completed_at" in e for e in errors)
    assert any("evidence" in e for e in errors)
