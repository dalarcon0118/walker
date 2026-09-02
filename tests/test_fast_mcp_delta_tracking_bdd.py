"""tests/test_fast_mcp_delta_tracking_bdd.py — BDD tests for delta_tracking.

Tests the domain entities (Spring, Spec), domain services (SpecValidator,
SpecStatusUpdater, SprintStatusUpdater, SpecService), and application
layer (SprintService) with in-memory mocks.

R1 compliance: only imports from fast_mcp.features.delta_tracking.*
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from fast_mcp.features.delta_tracking.domain.entities import Spring, Spec, _now
from fast_mcp.features.delta_tracking.domain.values import (
    SPRING_STATUS_FLOW,
    SPEC_STATUS_FLOW,
    is_valid_spring_status,
    is_valid_spec_status,
    spec_id_from_index,
    spec_doc_id,
    spring_id_from_chain_dir,
    TYPE_SPRING,
    TYPE_SPEC,
)
from fast_mcp.features.delta_tracking.domain.events import (
    DomainEvent,
    SpringCreated,
    SpecCreated,
    SpecUpdated,
    EventBus,
)
from fast_mcp.features.delta_tracking.domain.conflict import Conflict, ResolutionStrategy
from fast_mcp.features.delta_tracking.domain.spec_service import (
    SpecValidator,
    SpecStatusUpdater,
    SprintStatusUpdater,
    SpecService,
)
from fast_mcp.features.delta_tracking.application.ports.delta_repository import DeltaRepository
from fast_mcp.features.delta_tracking.application.services.sprint_service import SprintService


# ── In-memory repository mock ─────────────────────────────────────

class InMemoryDeltaRepo(DeltaRepository):
    """In-memory implementation for testing."""

    def __init__(self):
        self._springs: dict[str, Spring] = {}
        self._specs: dict[str, Spec] = {}

    def get_spring(self, spring_id: str) -> Spring | None:
        return self._springs.get(spring_id)

    def save_spring(self, spring: Spring) -> None:
        self._springs[spring.spring_id] = spring

    def list_springs(self, status: str = "") -> list[Spring]:
        springs = list(self._springs.values())
        if status:
            springs = [s for s in springs if s.status == status]
        return springs

    def get_active_spring(self) -> Spring | None:
        for s in self._springs.values():
            if s.status == "active" and s.active:
                return s
        return None

    def get_specs(self, spring_id: str, status: str = "") -> list[Spec]:
        specs = [s for s in self._specs.values() if s.spring_id == spring_id]
        if status:
            specs = [s for s in specs if s.status == status]
        return sorted(specs, key=lambda s: s.spec_index)

    def get_spec(self, spring_id: str, spec_id: str) -> Spec | None:
        for s in self._specs.values():
            if s.spring_id == spring_id and s.spec_id == spec_id:
                return s
        return None

    def save_spec(self, spec: Spec) -> None:
        self._specs[spec.doc_id] = spec

    def save_specs_batch(self, specs: list[Spec]) -> None:
        for s in specs:
            self._specs[s.doc_id] = s

    def delete_spec(self, spring_id: str, spec_id: str) -> None:
        key = f"{spring_id}:{spec_id}"
        self._specs.pop(key, None)

    def add_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        pass  # No-op for in-memory


# ── Domain: Values ────────────────────────────────────────────────

class TestValues:
    def test_spring_status_flow(self):
        assert SPRING_STATUS_FLOW == ["planning", "active", "reviewing", "closed"]

    def test_spec_status_flow(self):
        assert "draft" in SPEC_STATUS_FLOW
        assert "completed" in SPEC_STATUS_FLOW
        assert len(SPEC_STATUS_FLOW) == 8

    def test_is_valid_spring_status(self):
        assert is_valid_spring_status("planning")
        assert is_valid_spring_status("active")
        assert not is_valid_spring_status("invalid")

    def test_is_valid_spec_status(self):
        assert is_valid_spec_status("draft")
        assert is_valid_spec_status("specified")
        assert not is_valid_spec_status("invalid")

    def test_spec_id_from_index(self):
        assert spec_id_from_index(0) == "SPEC-001"
        assert spec_id_from_index(9) == "SPEC-010"
        assert spec_id_from_index(99) == "SPEC-100"

    def test_spec_doc_id(self):
        assert spec_doc_id("sprint-1", "SPEC-001") == "sprint-1:SPEC-001"

    def test_spring_id_from_chain_dir(self):
        assert spring_id_from_chain_dir("/path/to/sprint-1") == "sprint-1"
        assert spring_id_from_chain_dir("/path/to/sprint-1/") == "sprint-1"

    def test_node_types(self):
        assert TYPE_SPRING == "spring"
        assert TYPE_SPEC == "spec"


# ── Domain: Entities ──────────────────────────────────────────────

class TestSpec:
    def test_create_spec(self):
        spec = Spec(spring_id="sprint-1", objective="Test spec")
        assert spec.spring_id == "sprint-1"
        assert spec.objective == "Test spec"
        assert spec.status == "draft"
        assert spec.spec_id == "SPEC-001"
        assert spec.type == "spec"

    def test_spec_doc_id(self):
        spec = Spec(spring_id="sprint-1", objective="Test", spec_index=2)
        assert spec.doc_id == "sprint-1:SPEC-003"

    def test_spec_to_dict_roundtrip(self):
        spec = Spec(spring_id="sprint-1", objective="Test", domain="backend")
        d = spec.to_dict()
        restored = Spec.from_dict(d)
        assert restored.spring_id == spec.spring_id
        assert restored.objective == spec.objective
        assert restored.domain == spec.domain
        assert restored.status == spec.status

    def test_spec_invalid_status_raises(self):
        with pytest.raises(ValueError, match="Invalid spec status"):
            Spec(spring_id="sprint-1", objective="Test", status="invalid")

    def test_spec_with_all_fields(self):
        spec = Spec(
            spring_id="sprint-1",
            objective="Login screen",
            domain="frontend",
            spec_type="feature",
            spec_index=0,
            result_state="implemented",
            files=["src/auth.py"],
            status="specified",
            description="Create login screen",
            predicates={"login_works": True},
            preconditions=["auth service available"],
            acceptance_criteria=["user can login"],
            quality_gates=["tests pass"],
        )
        d = spec.to_dict()
        assert d["files"] == ["src/auth.py"]
        assert d["preconditions"] == ["auth service available"]


class TestSpring:
    def test_create_spring(self):
        spring = Spring(spring_id="sprint-1", goal="Build feature X")
        assert spring.spring_id == "sprint-1"
        assert spring.goal == "Build feature X"
        assert spring.status == "planning"
        assert spring.type == "spring"

    def test_spring_to_dict_roundtrip(self):
        spring = Spring(spring_id="sprint-1", goal="Test", project_id="walker")
        d = spring.to_dict()
        restored = Spring.from_dict(d)
        assert restored.spring_id == spring.spring_id
        assert restored.goal == spring.goal
        assert restored.project_id == spring.project_id

    def test_spring_invalid_status_raises(self):
        with pytest.raises(ValueError, match="Invalid spring status"):
            Spring(spring_id="sprint-1", status="invalid")

    def test_spring_from_dict_with_id(self):
        d = {"_id": "sprint-1", "goal": "Test"}
        spring = Spring.from_dict(d)
        assert spring.spring_id == "sprint-1"


# ── Domain: Events ────────────────────────────────────────────────

class TestEvents:
    def test_domain_event_base(self):
        event = DomainEvent()
        d = event.to_dict()
        assert d["event_type"] == "DomainEvent"
        assert "occurred_at" in d
        assert d["source"] == "delta_tracking"

    def test_spring_created_event(self):
        event = SpringCreated(spring_id="sprint-1", goal="Test", project_id="walker")
        d = event.to_dict()
        assert d["event_type"] == "SpringCreated"
        assert d["spring_id"] == "sprint-1"
        assert d["goal"] == "Test"

    def test_spec_created_event(self):
        event = SpecCreated(spec_id="SPEC-001", spring_id="sprint-1", objective="Test")
        d = event.to_dict()
        assert d["event_type"] == "SpecCreated"
        assert d["spec_id"] == "SPEC-001"

    def test_event_bus(self):
        bus = EventBus()
        received = []
        bus.subscribe(lambda e: received.append(e))
        event = SpringCreated(spring_id="sprint-1")
        bus.publish(event)
        assert len(received) == 1
        assert received[0].spring_id == "sprint-1"

    def test_event_bus_exception_safe(self):
        bus = EventBus()
        bus.subscribe(lambda e: 1 / 0)  # Raises ZeroDivisionError
        bus.subscribe(lambda e: None)  # Should still run
        received = []
        bus.subscribe(lambda e: received.append(e))
        bus.publish(SpringCreated())
        assert len(received) == 1


# ── Domain: Conflict ──────────────────────────────────────────────

class TestConflict:
    def test_create_conflict(self):
        c = Conflict(type="missing_required", node="SPRINT", message="field missing")
        assert c.type == "missing_required"
        assert not c.resolved

    def test_with_resolution(self):
        c = Conflict(type="missing_required", message="test")
        r = ResolutionStrategy(strategy="skip")
        resolved = c.with_resolution(r)
        assert resolved.resolved
        assert resolved.strategy == "skip"
        assert resolved.resolution is not None


# ── Domain: SpecService ───────────────────────────────────────────

class TestSpecValidator:
    def test_validate_fields_valid(self):
        data = {
            "result_state": "implemented",
            "task_description": "Build login",
            "predicates": ["login works"],
            "files": ["src/auth.py"],
            "preconditions": ["auth available"],
            "acceptance_criteria": ["user can login"],
            "quality_gates": ["tests pass"],
        }
        errors = SpecValidator.validate_fields(data)
        assert errors == []

    def test_validate_fields_missing(self):
        data = {}
        errors = SpecValidator.validate_fields(data)
        assert len(errors) == 7  # All required fields missing

    def test_validate_spec_valid(self):
        spec = {
            "spec_id": "SPEC-001",
            "objective": "Test",
            "status": "specified",
            "result_state": "implemented",
            "task_description": "Build",
            "validation": {"predicates": ["works"]},
            "impacted_artifacts": {"files": ["src/a.py"]},
            "preconditions": ["ok"],
            "acceptance_criteria": ["done"],
            "quality_gates": ["pass"],
        }
        errors = SpecValidator.validate_spec(spec)
        assert errors == []


class TestSpecStatusUpdater:
    def test_forward_transition(self):
        spec = {"status": "draft"}
        updated = SpecStatusUpdater.update(spec, "specified")
        assert updated["status"] == "specified"

    def test_backward_transition_raises(self):
        spec = {"status": "in_progress"}
        with pytest.raises(ValueError, match="Cannot move status back"):
            SpecStatusUpdater.update(spec, "draft")

    def test_invalid_status_raises(self):
        spec = {"status": "draft"}
        with pytest.raises(ValueError, match="Invalid status"):
            SpecStatusUpdater.update(spec, "invalid")


class TestSprintStatusUpdater:
    def test_can_transition_forward(self):
        ok, msg = SprintStatusUpdater.can_transition("planning", "active")
        assert ok

    def test_can_transition_same_status(self):
        ok, msg = SprintStatusUpdater.can_transition("active", "active")
        assert ok  # Idempotent

    def test_cannot_transition_backward(self):
        ok, msg = SprintStatusUpdater.can_transition("closed", "planning")
        assert not ok

    def test_invalid_status(self):
        ok, msg = SprintStatusUpdater.can_transition("planning", "invalid")
        assert not ok


class TestSpecServiceDomain:
    def test_merge_spec(self):
        spec = Spec(spring_id="sprint-1", objective="Old", status="draft")
        merged = SpecService.merge(spec, {"objective": "New", "status": "specified"})
        assert merged.objective == "New"
        assert merged.status == "specified"

    def test_refine_spec(self):
        spec = Spec(spring_id="sprint-1", objective="Old", status="draft")
        refined = SpecService.refine(spec, {"result_state": "implemented", "task_description": "Build login"})
        assert refined.status == "specified"  # Auto-upgrade when result_state provided
        assert refined.result_state == "implemented"


# ── Application: SprintService ────────────────────────────────────

class TestSprintService:
    def test_create_spring(self):
        repo = InMemoryDeltaRepo()
        svc = SprintService(project_root="/tmp", repo=repo)
        result = svc.create_spring("sprint-1", goal="Test", project_id="walker")
        assert result["spring_id"] == "sprint-1"
        assert result["goal"] == "Test"
        assert result["_id"] == "sprint-1"

    def test_get_spring(self):
        repo = InMemoryDeltaRepo()
        svc = SprintService(project_root="/tmp", repo=repo)
        svc.create_spring("sprint-1", goal="Test")
        result = svc.get_spring("sprint-1")
        assert result is not None
        assert result["spring_id"] == "sprint-1"

    def test_get_spring_not_found(self):
        repo = InMemoryDeltaRepo()
        svc = SprintService(project_root="/tmp", repo=repo)
        assert svc.get_spring("nonexistent") is None

    def test_list_springs(self):
        repo = InMemoryDeltaRepo()
        svc = SprintService(project_root="/tmp", repo=repo)
        svc.create_spring("sprint-1", goal="A")
        svc.create_spring("sprint-2", goal="B")
        springs = svc.list_springs()
        assert len(springs) == 2

    def test_add_spec(self):
        repo = InMemoryDeltaRepo()
        svc = SprintService(project_root="/tmp", repo=repo)
        svc.create_spring("sprint-1", goal="Test")
        spec = svc.add_spec("sprint-1", objective="Login screen")
        assert spec["spec_id"] == "SPEC-001"
        assert spec["objective"] == "Login screen"
        assert spec["spring_id"] == "sprint-1"

    def test_get_specs(self):
        repo = InMemoryDeltaRepo()
        svc = SprintService(project_root="/tmp", repo=repo)
        svc.create_spring("sprint-1", goal="Test")
        svc.add_spec("sprint-1", objective="Spec A")
        svc.add_spec("sprint-1", objective="Spec B")
        specs = svc.get_specs("sprint-1")
        assert len(specs) == 2
        assert specs[0]["spec_id"] == "SPEC-001"
        assert specs[1]["spec_id"] == "SPEC-002"

    def test_get_spec(self):
        repo = InMemoryDeltaRepo()
        svc = SprintService(project_root="/tmp", repo=repo)
        svc.create_spring("sprint-1", goal="Test")
        svc.add_spec("sprint-1", objective="Login screen")
        spec = svc.get_spec("sprint-1", "SPEC-001")
        assert spec is not None
        assert spec["objective"] == "Login screen"

    def test_update_spec(self):
        repo = InMemoryDeltaRepo()
        svc = SprintService(project_root="/tmp", repo=repo)
        svc.create_spring("sprint-1", goal="Test")
        svc.add_spec("sprint-1", objective="Old")
        updated = svc.update_spec("sprint-1", "SPEC-001", {"objective": "New"})
        assert updated["objective"] == "New"

    def test_update_spec_status(self):
        repo = InMemoryDeltaRepo()
        svc = SprintService(project_root="/tmp", repo=repo)
        svc.create_spring("sprint-1", goal="Test")
        svc.add_spec("sprint-1", objective="Test")
        updated = svc.update_spec_status("sprint-1", "SPEC-001", "specified")
        assert updated["status"] == "specified"

    def test_load_tasks(self):
        repo = InMemoryDeltaRepo()
        svc = SprintService(project_root="/tmp", repo=repo)
        svc.create_spring("sprint-1", goal="Test")
        svc.add_spec("sprint-1", objective="Spec A")
        tasks = svc.load_tasks("/tmp/sprint-1")
        assert len(tasks) == 1
        assert tasks[0]["objective"] == "Spec A"

    def test_sprint_progress(self):
        repo = InMemoryDeltaRepo()
        svc = SprintService(project_root="/tmp", repo=repo)
        svc.create_spring("sprint-1", goal="Test")
        svc.add_spec("sprint-1", objective="Spec A")
        svc.add_spec("sprint-1", objective="Spec B")
        progress = svc.get_sprint_progress("sprint-1")
        assert progress["total"] == 2
        assert progress["completed"] == 0

    def test_no_repo_returns_empty(self):
        svc = SprintService()
        assert svc.get_spring("x") is None
        assert svc.list_springs() == []
        assert svc.get_specs("x") == []
        assert svc.load_tasks("/x") == []
