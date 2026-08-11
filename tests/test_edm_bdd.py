"""Tests para EvolutionaryDocumentModel (EDM)."""
from __future__ import annotations

import pytest

from features.delta_tracking.application.evolutionary_document_model import (
    EvolutionaryDocumentModel,
)
from features.delta_tracking.domain.entities import Spec


class TestEvolutionaryDocumentModel:
    def test_transform_simple_spec(self):
        edm = EvolutionaryDocumentModel(chain_dir=".")
        spec = Spec(
            spring_id="spring-001",
            objective="Test spec",
            status="draft",
        )
        transformed, memory_ops = edm.transform(spec, "SPRINT_PLANNING.TASK_SPEC", {"problem_statement": "test problem"})
        assert transformed.status == "specified"
        assert transformed.problem_statement == "test problem"
        assert len(memory_ops) > 0

    def test_transform_approved_decision(self):
        edm = EvolutionaryDocumentModel(chain_dir=".")
        spec = Spec(
            spring_id="spring-001",
            objective="Test spec",
            status="specified",
        )
        transformed, memory_ops = edm.transform(spec, "SPRINT_PLANNING.TASK_REVIEW", {"decision": {"statement": "test"}})
        assert transformed.status == "approved"
        assert transformed.decision == {"statement": "test"}

    def test_invalid_transition_blocked(self):
        edm = EvolutionaryDocumentModel(chain_dir=".")
        spec = Spec(
            spring_id="spring-001",
            objective="Test spec",
            status="completed",
        )
        transformed, memory_ops = edm.transform(spec, "SPRINT_PLANNING.TASK_SPEC", {"problem_statement": "test"})
        assert transformed.status == "completed"
        assert memory_ops == []

    def test_memory_ops_per_status(self):
        edm = EvolutionaryDocumentModel(chain_dir=".")
        spec = Spec(
            spring_id="spring-001",
            objective="Test spec",
            status="draft",
        )
        _, memory_ops = edm.transform(spec, "SPRINT_PLANNING.TASK_SPEC", {"problem_statement": "test"})
        targets = {op["target"] for op in memory_ops}
        assert "delta" in targets
        assert "ckg" in targets
        assert "cpg" in targets
        assert "trace" in targets
