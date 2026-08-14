"""tests/test_workflow_e2e_bdd.py — E2E test: flujo completo de workflow + Spec + memorias.

Recorre el camino completo:
  SPRINT(PLANNING → TASK_SPEC → TASK_REVIEW) →
  DESIGN(GATHER) →
  ANALYZE(GATHER) →
  IMPLEMENT(USER_GATE → WORK → CODE_REVIEW_SCAN → CODE_REVIEW_RESULT → REPORT_GATE) →
  VERIFY(USER_GATE → QA_RUN → PO_ACCEPT) →
  SPRINT(ACTIVE)

Valida en cada paso:
  - Nodo existe y tiene side effects declarados
  - Arista/gate conecta correctamente con el siguiente nodo
  - WorkflowState evoluciona (current_node, current_sub_state)
  - Spec evoluciona en DeltaGraph (status, campos por fase)
  - MemoryOrchestrator genera las memory_ops correctas por estado
  - ADR se crea en CKG cuando approval_type=decision
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.engine.domain.entities import WorkflowGraph, WorkflowState
from core.engine.infrastructure.repositories import WorkflowStateRepository, WorkflowRepository
from features.delta_tracking import spring_service
from features.delta_tracking.application.evolutionary_document_model import EvolutionaryDocumentModel
from features.delta_tracking.domain.entities import Spec
from features.main.task_execution.side_effects.create_adr import create_adr_from_spec
from tests.conftest import make_chain_dir, make_walker_project


# ── Fixtures y helpers ────────────────────────────────────────────────

def _setup_project(tmp_path: Path) -> str:
    """Crea el proyecto walker cognitivo (builder compartido en conftest)."""
    return make_walker_project(tmp_path)


def _setup_chain_dir(tmp_path: Path) -> str:
    """Crea un chain_dir mínimo (builder compartido en conftest)."""
    return make_chain_dir(tmp_path, "sprint")


def _setup_spring_and_spec(chain_dir: str):
    svc = spring_service(chain_dir=chain_dir)
    assert svc is not None
    spring_id = svc.get_spring_id(chain_dir)
    svc.create_spring(spring_id, goal="Sprint E2E test", project_id="walker", session_id="s1")
    svc.add_spec(spring_id, objective="E2E test spec", domain="test", spec_type="feature", spec_index=0, status="draft")
    return svc, spring_id, "SPEC-001"


def _load_workflow_graph(project_root: str) -> WorkflowGraph:
    repo = WorkflowRepository()
    return repo.load(project_root)


def _write_workflow_state(chain_dir: str, state: WorkflowState) -> None:
    repo = WorkflowStateRepository()
    repo.save(chain_dir, state)


def _build_initial_state(chain_dir: str, node: str = "SPRINT", sub_state: str = "PLANNING") -> WorkflowState:
    return WorkflowState(
        current_node=node,
        current_sub_state=sub_state,
        sub_state_index=0,
        sub_state_data={},
        history=[],
        context={},
        version="5.0",
        updated_at="",
        node_executions=[],
        conflicts=[],
        blocked_at="",
        block_reason="",
        block_since="",
        pending_events=[],
        block_timeout_ms=0,
        pending_signals=[],
        signal_history=[],
        process_data={},
    )


class FakeCtx:
    def __init__(self, chain_dir: str, node_key: str = ""):
        self.chain_dir = chain_dir
        self.task_id = "SPEC-001"
        self.baggage = {"node_key": node_key} if node_key else {}


# ── Tests E2E ────────────────────────────────────────────────────────

class TestWorkflowE2E:
    """Flujo completo del workflow con validación de nodos, aristas,
    side effects, WorkflowState y cambios en la Spec."""

    def test_workflow_graph_has_complete_path(self, tmp_path):
        """El grafo del workflow contiene el camino completo SPRINT → DESIGN → ANALYZE → IMPLEMENT → VERIFY → SPRINT."""
        project_root = _setup_project(tmp_path)
        graph = _load_workflow_graph(project_root)

        for node_id in ("SPRINT", "DESIGN", "ANALYZE", "IMPLEMENT", "VERIFY"):
            assert node_id in graph.nodes, f"Nodo {node_id} falta en el workflow"

        expected_edges = [
            ("SPRINT", "start_task", "DESIGN"),
            ("DESIGN", "design_approved", "ANALYZE"),
            ("ANALYZE", "analysis_approved", "IMPLEMENT"),
            ("IMPLEMENT", "implementation_done", "VERIFY"),
            ("VERIFY", "task_complete", "SPRINT"),
        ]
        for source, gate_name, target in expected_edges:
            gate = graph.find_gate(source, gate_name)
            assert gate is not None, f"Gate {gate_name} desde {source} no existe"
            assert gate.target_node == target, f"Gate {gate_name} debería ir a {target}, no a {gate.target_node}"

    def test_each_node_has_side_effects(self, tmp_path):
        """Cada nodo del workflow declara side effects en sus sub-estados."""
        project_root = _setup_project(tmp_path)
        graph = _load_workflow_graph(project_root)

        nodes_with_side_effects = ["SPRINT", "DESIGN", "ANALYZE", "IMPLEMENT", "VERIFY"]
        for node_id in nodes_with_side_effects:
            node = graph.get_node(node_id)
            assert node is not None, f"Nodo {node_id} no existe"
            assert len(node.sub_states) > 0, f"Nodo {node_id} no tiene sub-estados"
            has_side_effects = any(len(ss.side_effects) > 0 for ss in node.sub_states)
            assert has_side_effects, f"Nodo {node_id} no tiene side effects en ningún sub-estado"

    def test_full_workflow_spec_transitions(self, tmp_path):
        """La Spec atraviesa todos los estados del workflow correctamente."""
        project_root = _setup_project(tmp_path)
        chain_dir = _setup_chain_dir(tmp_path)
        svc, spring_id, spec_id = _setup_spring_and_spec(chain_dir)

        spec = svc.get_spec(spring_id, spec_id)
        assert spec is not None
        spec_dict = spec if isinstance(spec, dict) else spec.to_dict()
        assert spec_dict.get("status") == "draft"

        path = [
            ("SPRINT_PLANNING.TASK_SPEC", "specified"),
            ("SPRINT_PLANNING.TASK_REVIEW", "approved"),
            ("DESIGN.GATHER", "in_progress"),
            ("ANALYZE.GATHER", "in_progress"),
            ("IMPLEMENT.USER_GATE", "in_progress"),
            ("IMPLEMENT.WORK", "in_progress"),
            ("IMPLEMENT.CODE_REVIEW_SCAN", "in_review"),
            ("IMPLEMENT.CODE_REVIEW_RESULT", "in_review"),
            ("IMPLEMENT.REPORT_GATE", "completed"),
        ]

        edm = EvolutionaryDocumentModel(chain_dir=chain_dir)
        current_spec = Spec.from_dict(spec_dict)

        for node_key, expected_status in path:
            transformed, _ = edm.transform(current_spec, node_key, {})
            assert transformed.status == expected_status, (
                f"Nodo {node_key}: esperado {expected_status}, obtenido {transformed.status}"
            )
            current_spec = transformed

    def test_workflow_state_evolves_through_nodes(self, tmp_path):
        """WorkflowState evoluciona a través de los nodos del workflow."""
        chain_dir = _setup_chain_dir(tmp_path)

        state = _build_initial_state(chain_dir, "SPRINT", "PLANNING")
        _write_workflow_state(chain_dir, state)

        repo = WorkflowStateRepository()
        loaded = repo.load(chain_dir)
        assert loaded.current_node == "SPRINT"
        assert loaded.current_sub_state == "PLANNING"

        state2 = state.record_submit("TASK_SPEC", {"task_id": "SPEC-001", "status": "specified"})
        state2 = replace(state2, current_sub_state="TASK_SPEC")
        _write_workflow_state(chain_dir, state2)
        loaded2 = repo.load(chain_dir)
        assert loaded2.current_sub_state == "TASK_SPEC"

        state3 = state2.record_submit("TASK_REVIEW", {"decision": "approve"})
        state3 = replace(state3, current_sub_state="TASK_REVIEW")
        _write_workflow_state(chain_dir, state3)
        loaded3 = repo.load(chain_dir)
        assert loaded3.current_sub_state == "TASK_REVIEW"

    def test_memory_orchestrator_generates_correct_ops_per_phase(self, tmp_path):
        """MemoryOrchestrator genera las memory_ops correctas para cada estado de Spec."""
        chain_dir = _setup_chain_dir(tmp_path)

        spec_spec = Spec.from_dict({
            "spring_id": "sprint-1", "objective": "Test", "domain": "test",
            "spec_type": "feature", "spec_index": 0, "status": "specified",
        })
        ops_spec = EvolutionaryDocumentModel(chain_dir=chain_dir)._memory_ops_for_status("specified", spec_spec)
        assert len(ops_spec) == 4
        assert ops_spec[0] == {"target": "delta", "op": "upsert_spec", "data": {"status": "specified"}}
        assert {"target": "ckg", "op": "save", "data": {"type": "problem", "id": spec_spec.spec_id}} in ops_spec
        assert {"target": "trace", "op": "emit", "event": "spec_specified"} in ops_spec

        ops_approved = EvolutionaryDocumentModel(chain_dir=chain_dir)._memory_ops_for_status("approved", spec_spec)
        assert len(ops_approved) == 3
        assert {"target": "ckg", "op": "save", "data": {"type": "decision", "id": spec_spec.spec_id}} in ops_approved
        assert {"target": "trace", "op": "emit", "event": "decision_approved"} in ops_approved

        ops_review = EvolutionaryDocumentModel(chain_dir=chain_dir)._memory_ops_for_status("in_review", spec_spec)
        assert len(ops_review) == 3
        assert {"target": "cpg", "op": "scope", "data": {"scope": "code_review"}} in ops_review
        assert {"target": "trace", "op": "emit", "event": "code_review_scanned"} in ops_review

        ops_completed = EvolutionaryDocumentModel(chain_dir=chain_dir)._memory_ops_for_status("completed", spec_spec)
        assert len(ops_completed) == 3
        assert {"target": "ckg", "op": "documenting_knowledge"} in ops_completed
        assert {"target": "trace", "op": "emit", "event": "spec_completed"} in ops_completed

    def test_adr_created_at_approval_phase(self, tmp_path):
        """El ADR se crea en CKG cuando la Spec se aprueba como decision."""
        chain_dir = _setup_chain_dir(tmp_path)
        svc, spring_id, spec_id = _setup_spring_and_spec(chain_dir)

        svc.update_spec(spring_id, spec_id, {
            "approval_type": "decision",
            "decision_data": {
                "decision": "Usar microservicios",
                "architecture": "API Gateway",
                "implementation": "3 servicios",
            },
            "status": "approved",
        })

        state = _build_initial_state(chain_dir, "SPRINT", "TASK_REVIEW")
        state = state.record_submit("TASK_SPEC", {
            "approval_type": "decision",
            "decision_data": {
                "decision": "Usar microservicios",
                "architecture": "API Gateway",
                "implementation": "3 servicios",
            },
        })
        state = state.record_submit("TASK_REVIEW", {
            "decision": "approve",
            "approval_type": "decision",
            "approval_data": {
                "decision": "Usar microservicios",
                "architecture": "API Gateway",
                "implementation": "3 servicios",
            },
        })
        _write_workflow_state(chain_dir, state)

        ctx = FakeCtx(chain_dir=chain_dir, node_key="SPRINT_PLANNING.TASK_REVIEW")
        result = create_adr_from_spec(ctx, {"approval_type": "NODE.TASK_SPEC.output.approval_type"})
        assert result["ok"] is True
        assert result.get("metadata", {}).get("adr_id") is not None

        svc_after = spring_service(chain_dir=chain_dir)
        spec_after = svc_after.get_spec(spring_id, spec_id)
        spec_after_dict = spec_after if isinstance(spec_after, dict) else spec_after.to_dict()
        related = spec_after_dict.get("related_decisions", [])
        assert len(related) > 0, "La Spec debería estar vinculada a un ADR"
        generated_from = spec_after_dict.get("generated_from", "")
        assert "SPEC" in generated_from, f"generated_from debería referenciar la Spec: {generated_from}"

    def test_full_path_spec_state_sequence(self, tmp_path):
        """Secuencia completa de estados de Spec a través de todo el workflow."""
        chain_dir = _setup_chain_dir(tmp_path)

        path = [
            ("SPRINT_PLANNING.PLANNING", "draft"),
            ("SPRINT_PLANNING.TASK_SPEC", "specified"),
            ("SPRINT_PLANNING.TASK_REVIEW", "approved"),
            ("DESIGN.GATHER", "in_progress"),
            ("ANALYZE.GATHER", "in_progress"),
            ("IMPLEMENT.USER_GATE", "in_progress"),
            ("IMPLEMENT.WORK", "in_progress"),
            ("IMPLEMENT.CODE_REVIEW_SCAN", "in_review"),
            ("IMPLEMENT.CODE_REVIEW_RESULT", "in_review"),
            ("IMPLEMENT.REPORT_GATE", "completed"),
            ("VERIFY.USER_GATE", "completed"),
            ("VERIFY.QA_RUN", "completed"),
            ("VERIFY.PO_ACCEPT", "completed"),
        ]

        edm = EvolutionaryDocumentModel(chain_dir=chain_dir)
        spec = Spec.from_dict({
            "spring_id": "sprint-1", "objective": "E2E", "domain": "test",
            "spec_type": "feature", "spec_index": 0, "status": "draft",
        })

        for node_key, expected_status in path:
            transformed, _ = edm.transform(spec, node_key, {})
            assert transformed.status == expected_status, (
                f"Nodo {node_key}: esperado {expected_status}, obtenido {transformed.status}"
            )
            spec = transformed
