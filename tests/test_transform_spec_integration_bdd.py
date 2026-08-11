"""Tests de integración para el handler transform_spec con delta real."""
from __future__ import annotations

import json
import os

import pytest

from features.engine.infrastructure.side_effects import SideEffectEngine
from features.engine.bootstrap import bootstrap_side_effects
from features.delta_tracking import spring_service
from tests.conftest import make_chain_dir


class MockCtx:
    def __init__(self, chain_dir: str, node_key: str = ""):
        self.chain_dir = chain_dir
        self.task_id = "SPEC-001"
        self.baggage = {"node_key": node_key} if node_key else {}


def _setup_project(tmp_path):
    """Crea un chain_dir mínimo (builder compartido en conftest)."""
    return make_chain_dir(tmp_path, "project", project_id="test")


def _write_workflow_state(chain_dir: str, sub_state_data: dict):
    state_path = os.path.join(chain_dir, ".workflow-state.json")
    state = {
        "current_node": "SPRINT",
        "current_sub_state": "TASK_SPEC",
        "sub_state_index": 0,
        "sub_state_data": sub_state_data,
        "history": [],
        "context": {},
        "version": "5.0",
        "updated_at": "",
        "node_executions": [],
        "conflicts": [],
        "blocked_at": "",
        "block_reason": "",
        "block_since": "",
        "pending_events": [],
        "block_timeout_ms": 0,
        "pending_signals": [],
        "signal_history": [],
        "process_data": {},
    }
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)


class TestTransformSpecIntegration:
    """Integración: SideEffectEngine + handler transform_spec + delta real."""

    def test_handler_registered_in_bootstrap(self):
        engine = SideEffectEngine()
        bootstrap_side_effects(engine)
        assert "transform_spec" in engine._handlers

    def test_transform_spec_specified_persists_in_delta(self, tmp_path):
        chain_dir = _setup_project(tmp_path)
        svc = spring_service(chain_dir=chain_dir)
        assert svc is not None
        spring_id = svc.get_spring_id(chain_dir)
        svc.create_spring(spring_id, goal="Test goal", project_id="test")
        svc.add_spec(spring_id, objective="Test spec", domain="test", spec_type="feature", spec_index=0, status="draft")

        ctx = MockCtx(chain_dir=chain_dir, node_key="SPRINT_PLANNING.TASK_SPEC")
        params = {
            "status": "specified",
            "fields": {
                "problem_statement": "Test problem",
                "evidence": ["e1"],
                "predicates": ["p1"],
            },
        }

        engine = SideEffectEngine()
        bootstrap_side_effects(engine)
        result = engine._handlers["transform_spec"](ctx, params)
        assert result.get("ok") is True, result.get("error")
        assert result.get("status") == "specified"
        assert "problem_statement" in result.get("fields_applied", [])

    def test_transform_spec_invalid_transition_returns_error(self, tmp_path):
        chain_dir = _setup_project(tmp_path)
        svc = spring_service(chain_dir=chain_dir)
        assert svc is not None
        svc.create_spring("Test goal", project_id="test")

        ctx = MockCtx(chain_dir=chain_dir, node_key="UNKNOWN_NODE")
        params = {"status": "unknown_status", "fields": {}}

        engine = SideEffectEngine()
        bootstrap_side_effects(engine)
        result = engine._handlers["transform_spec"](ctx, params)
        assert result.get("ok") is False
        assert "error" in result

    def test_transform_spec_no_specs_returns_error(self, tmp_path):
        chain_dir = _setup_project(tmp_path)

        ctx = MockCtx(chain_dir=chain_dir, node_key="SPRINT_PLANNING.TASK_SPEC")
        params = {"status": "specified", "fields": {"problem_statement": "test"}}

        engine = SideEffectEngine()
        bootstrap_side_effects(engine)
        result = engine._handlers["transform_spec"](ctx, params)
        assert result.get("ok") is False
        assert "No specs found" in result.get("error", "")

    def test_transform_spec_resolves_node_output_references(self, tmp_path):
        chain_dir = _setup_project(tmp_path)
        svc = spring_service(chain_dir=chain_dir)
        assert svc is not None
        spring_id = svc.get_spring_id(chain_dir)
        svc.create_spring(spring_id, goal="Test goal", project_id="test")
        svc.add_spec(spring_id, objective="Test spec", domain="test", spec_type="feature", spec_index=0, status="draft")

        _write_workflow_state(chain_dir, {
            "SPRINT.TASK_SPEC": {
                "fields": {
                    "approval_type": "decision",
                    "decision_data": {
                        "decision": "Usar microservicios",
                        "architecture": "API Gateway + servicios independientes",
                        "implementation": "Implementar 3 servicios nuevos",
                        "quality_gates": ["tests", "lint", "security"],
                    },
                }
            }
        })

        ctx = MockCtx(chain_dir=chain_dir, node_key="SPRINT_PLANNING.TASK_REVIEW")
        params = {
            "status": "approved",
            "fields": {
                "decision": "NODE.TASK_SPEC.output.decision_data",
                "architecture": "NODE.TASK_SPEC.output.decision_data",
                "implementation": "NODE.TASK_SPEC.output.decision_data",
            },
        }

        engine = SideEffectEngine()
        bootstrap_side_effects(engine)
        result = engine._handlers["transform_spec"](ctx, params)
        assert result.get("ok") is True, result.get("error")
        assert result.get("status") == "approved"

        svc_after = spring_service(chain_dir=chain_dir)
        specs = svc_after.get_specs(spring_id)
        assert specs
        spec = specs[0]
        assert spec.get("decision") == "Usar microservicios"
        assert spec.get("architecture") == "API Gateway + servicios independientes"
        assert spec.get("implementation") == "Implementar 3 servicios nuevos"
