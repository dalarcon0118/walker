"""Tests de integración para el handler transform_spec con delta real."""
from __future__ import annotations

import json
import os

import pytest

from shared.core.engine.infrastructure.side_effects import SideEffectEngine
from features.main.task_execution.side_effects import register_task_execution_side_effects
from features.delta_tracking import spring_service

from features.delta_tracking.domain.entities import Spec
from tests.conftest import make_chain_dir


def _env_error(result: dict) -> str:
    """Extrae el error del envelope @as_side_effect (SPEC-014 F2).

    El envelope estandarizado mueve el detalle a metadata.error; el ok
    queda a nivel top-level. Los tests escritos contra el shape legacy
    (error a nivel top-level) leen via este helper.
    """
    return _env_field(result, "error")


def _env_field(result: dict, key: str):
    """Lee un campo del envelope (busca en top-level y en metadata)."""
    if key in result:
        return result.get(key)
    return (result.get("metadata") or {}).get(key)


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
        register_task_execution_side_effects(engine)
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
        register_task_execution_side_effects(engine)
        result = engine._handlers["transform_spec"](ctx, params)
        assert result.get("ok") is True, _env_error(result)
        assert result.get("status") == "specified"
        assert "problem_statement" in _env_field(result, "fields_applied") or []

    def test_transform_spec_invalid_transition_returns_error(self, tmp_path):
        chain_dir = _setup_project(tmp_path)
        svc = spring_service(chain_dir=chain_dir)
        assert svc is not None
        svc.create_spring("Test goal", project_id="test")

        ctx = MockCtx(chain_dir=chain_dir, node_key="UNKNOWN_NODE")
        params = {"status": "unknown_status", "fields": {}}

        engine = SideEffectEngine()
        register_task_execution_side_effects(engine)
        result = engine._handlers["transform_spec"](ctx, params)
        assert result.get("ok") is False
        assert _env_error(result)

    def test_transform_spec_no_specs_returns_error(self, tmp_path):
        chain_dir = _setup_project(tmp_path)

        ctx = MockCtx(chain_dir=chain_dir, node_key="SPRINT_PLANNING.TASK_SPEC")
        params = {"status": "specified", "fields": {"problem_statement": "test"}}

        engine = SideEffectEngine()
        register_task_execution_side_effects(engine)
        result = engine._handlers["transform_spec"](ctx, params)
        assert result.get("ok") is False
        assert "No specs found" in _env_error(result)

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
        register_task_execution_side_effects(engine)
        result = engine._handlers["transform_spec"](ctx, params)
        assert result.get("ok") is True, _env_error(result)
        assert result.get("status") == "approved"

        svc_after = spring_service(chain_dir=chain_dir)
        specs = svc_after.get_specs(spring_id)
        assert specs
        spec = specs[0]
        assert spec.get("decision") == "Usar microservicios"
        assert spec.get("architecture") == "API Gateway + servicios independientes"
        assert spec.get("implementation") == "Implementar 3 servicios nuevos"

    # ── SPEC-005: constitución de la spec approved (invariants/project_snapshot) ──

    def test_spec_entity_approved_constitution_roundtrip(self):
        """La entidad Spec persiste invariants/project_snapshot en approved."""
        spec = Spec(
            spring_id="s",
            objective="o",
            spec_index=0,
            status="approved",
            invariants=["INV-001: WorkflowState nunca se pierde", "INV-012: side effects atómicos"],
            project_snapshot={
                "cpg_top": [{"name": "AuthService", "type": "class"}],
                "delta_refs": ["SPEC-001"],
                "ckg_decisions": 15,
            },
        )
        data = spec.to_dict()
        assert data["invariants"] == [
            "INV-001: WorkflowState nunca se pierde",
            "INV-012: side effects atómicos",
        ]
        assert data["project_snapshot"]["cpg_top"][0]["name"] == "AuthService"
        restored = Spec.from_dict(data)
        assert restored.invariants == spec.invariants
        assert restored.project_snapshot == spec.project_snapshot
        assert restored.status == "approved"

    def test_spec_entity_constitution_defaults_empty(self):
        """Los campos nuevos no rompen specs existentes (defaults vacíos)."""
        spec = Spec(spring_id="s", objective="o", spec_index=0, status="draft")
        assert spec.invariants == []
        assert spec.project_snapshot == {}
        data = spec.to_dict()
        assert data["invariants"] == []
        assert data["project_snapshot"] == {}
        assert Spec.from_dict(data).invariants == []

    def test_transform_spec_approved_persists_constitution_in_delta(self, tmp_path):
        """Approved persiste invariants/project_snapshot en el índice delta."""
        chain_dir = _setup_project(tmp_path)
        svc = spring_service(chain_dir=chain_dir)
        assert svc is not None
        spring_id = svc.get_spring_id(chain_dir)
        svc.create_spring(spring_id, goal="Test goal", project_id="test")
        svc.add_spec(
            spring_id, objective="Test spec", domain="test",
            spec_type="feature", spec_index=0, status="draft",
        )

        _write_workflow_state(chain_dir, {
            "SPRINT.TASK_SPEC": {
                "fields": {
                    "decision_data": {
                        "decision": "Usar composition root único",
                        "architecture": "bootstrap(separated=True)",
                        "implementation": "Unificar dispatchers",
                        "invariants": ["INV-001", "INV-012"],
                        "project_snapshot": {
                            "cpg_top": [{"name": "App", "type": "file"}],
                            "delta_refs": ["SPEC-001"],
                            "ckg_decisions": 15,
                        },
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
                "invariants": "NODE.TASK_SPEC.output.decision_data",
                "project_snapshot": "NODE.TASK_SPEC.output.decision_data",
            },
        }

        engine = SideEffectEngine()
        register_task_execution_side_effects(engine)
        result = engine._handlers["transform_spec"](ctx, params)
        assert result.get("ok") is True, _env_error(result)
        assert result.get("status") == "approved"
        assert "invariants" in _env_field(result, "fields_applied") or []
        assert "project_snapshot" in _env_field(result, "fields_applied") or []

        svc_after = spring_service(chain_dir=chain_dir)
        spec = svc_after.get_specs(spring_id)[0]
        assert spec.get("invariants") == ["INV-001", "INV-012"]
        assert spec.get("project_snapshot", {}).get("cpg_top") == [
            {"name": "App", "type": "file"}
        ]
        assert spec.get("project_snapshot", {}).get("ckg_decisions") == 15
        assert spec.get("status") == "approved"

    def test_sprint_node_approval_contract_includes_constitution(self):
        """El nodo real de aprobación (sprint.node.json) pide y persiste la
        constitución de la spec approved (SPEC-005 #2)."""
        with open("core/engine/workflow/nodes/sprint.node.json") as f:
            node = json.load(f)
        sub_states = {s["key"]: s for s in node["sub_states"]}
        decision_data = sub_states["TASK_SPEC"]["output_schema"]["properties"][
            "decision_data"
        ]["properties"]
        assert "invariants" in decision_data
        assert "project_snapshot" in decision_data
        transform = next(
            se
            for se in sub_states["TASK_REVIEW"]["side_effects"]
            if se["type"] == "transform_spec"
            and se["params"].get("status") == "approved"
        )
        fields = transform["params"]["fields"]
        assert fields["invariants"] == "NODE.TASK_SPEC.output.decision_data"
        assert fields["project_snapshot"] == "NODE.TASK_SPEC.output.decision_data"

    def test_transform_spec_unresolved_ref_fails_without_persisting(self, tmp_path):
        """Ref NODE. sin resolver → ok=False y NADA se persiste (ni refs crudas)."""
        chain_dir = _setup_project(tmp_path)
        svc = spring_service(chain_dir=chain_dir)
        assert svc is not None
        spring_id = svc.get_spring_id(chain_dir)
        svc.create_spring(spring_id, goal="Test goal", project_id="test")
        svc.add_spec(
            spring_id, objective="Test spec", domain="test",
            spec_type="feature", spec_index=0, status="draft",
        )

        ctx = MockCtx(chain_dir=chain_dir, node_key="SPRINT_PLANNING.TASK_REVIEW")
        params = {
            "status": "approved",
            "fields": {
                "decision": "NODE.NO_EXISTE.output.decision_data",
                "invariants": "NODE.NO_EXISTE.output.invariants",
            },
        }

        engine = SideEffectEngine()
        register_task_execution_side_effects(engine)
        result = engine._handlers["transform_spec"](ctx, params)
        assert result.get("ok") is False
        assert "NODE." in _env_error(result)
        assert "decision" in _env_field(result, "unresolved_refs") or []
        # El envelope mueve el detalle a metadata; nada de lo declarado se persiste
        assert "invariants" in _env_field(result, "unresolved_refs") or []

        # La spec queda intacta: sin NODE. crudas en el delta y sin decision.
        svc_after = spring_service(chain_dir=chain_dir)
        spec = svc_after.get_specs(spring_id)[0]
        assert spec.get("status") == "draft"
        assert spec.get("decision") in (None, {})
        raw = json.dumps(spec)
        assert "NODE." not in raw
