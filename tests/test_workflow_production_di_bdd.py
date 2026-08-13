"""tests/test_workflow_production_di_bdd.py — Regresión del camino real CLI/MCP.

Verifica que el flujo de PRODUCCIÓN (get_module_service("workflow").submit,
usado por handle_submit del CLI y por el tool MCP 'submit') ejecuta las
tasks REALES del workflow. ANTES este camino devolvía submitted=True sin
ejecutar plan_sprint: _build_workflow_services construía SubmitDataUseCase
sin task_registry y el fallback silencioso de _execute_task hacía que el
workflow avanzara congelado en PLANNING.

Fix (capa_rout.md D1): bootstrap() registra task_registry/side_effect_engine
como servicios DI y _build_workflow_services los inyecta en los use cases.
"""
from __future__ import annotations

import os

from core.engine.domain.entities import WorkflowState
from core.engine.infrastructure.repositories import WorkflowStateRepository
from tests.conftest import make_chain_dir


def _write_workflow(chain_dir: str) -> None:
    """Workflow mínimo con dos sub-estados con action real."""
    import json

    workflow = {
        "label": "DI Probe",
        "initial_state": "SPRINT_PLANNING",
        "nodes": {
            "SPRINT_PLANNING": {
                "sub_states": [
                    {
                        "key": "PLANNING",
                        "execution": "llm_task",
                        "action": "plan_sprint",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                        "on_success": "TASK_SPEC",
                        "side_effects": [],
                    },
                    {
                        "key": "TASK_SPEC",
                        "execution": "llm_task",
                        "action": "spec_task",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                        "side_effects": [],
                    },
                ]
            }
        },
        "edges": [],
        "on_events": {},
    }
    parent = os.path.dirname(os.path.dirname(chain_dir))
    wf_dir = os.path.join(parent, "workflow.v3")
    os.makedirs(wf_dir, exist_ok=True)
    with open(os.path.join(wf_dir, "workflow.json"), "w") as f:
        json.dump(workflow, f, indent=2)


def _setup_state(chain_dir: str) -> None:
    state = WorkflowState(
        current_node="SPRINT_PLANNING",
        current_sub_state="PLANNING",
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
    WorkflowStateRepository().save(chain_dir, state)


class TestProductionDICamino:
    """El camino de producción ejecuta tasks reales (no se congela)."""

    def test_workflow_submit_ejecuta_task_real(self, tmp_path):
        """get_module_service('workflow').submit ejecuta plan_sprint real y
        avanza a TASK_SPEC (antes: submitted=True sin task, congelado)."""
        from core.engine.bootstrap import get_module_service

        chain_dir = make_chain_dir(tmp_path, "di-probe", project_name="walker")
        _write_workflow(chain_dir)
        _setup_state(chain_dir)

        svc = get_module_service("workflow")
        result = svc.submit(chain_dir, fields={"goal": "g", "tasks": []})

        assert "error" not in result, result.get("error")
        # El fix del DI: la task REAL se ejecutó (no fallback silencioso)
        assert result.get("task_executed") is True, f"task no ejecutada: {result}"
        assert result.get("task_action") == "plan_sprint"
        # plan_sprint real delega a PlanSprintUseCase → estado planned
        task_result = str(result.get("task_result", ""))
        assert "planned" in task_result, f"plan_sprint no devolvió planned: {task_result}"

        state = WorkflowStateRepository().load(chain_dir)
        assert state.current_sub_state == "TASK_SPEC", (
            f"no avanzó a TASK_SPEC: {state.current_sub_state}"
        )

    def test_workflow_submit_ejecuta_spec_task(self, tmp_path):
        """Encadenado: spec_task real (SpecifySpecUseCase) también se ejecuta
        en el camino de producción — valida el registro completo del DI."""
        from core.engine.bootstrap import get_module_service

        chain_dir = make_chain_dir(tmp_path, "di-probe2", project_name="walker")
        _write_workflow(chain_dir)
        _setup_state(chain_dir)

        svc = get_module_service("workflow")
        result = svc.submit(chain_dir, fields={"goal": "g", "tasks": []})
        assert result.get("task_executed") is True

        # Estado avanzó a TASK_SPEC; el siguiente submit ejecuta spec_task
        state = WorkflowStateRepository().load(chain_dir)
        assert state.current_sub_state == "TASK_SPEC"

        result2 = svc.submit(
            chain_dir,
            fields={
                "task_id": "T-1",
                "objective": "obj",
                "domain": "test",
                "spec_type": "feature",
                "spec_index": 0,
                "status": "specified",
                "acceptance_criteria": ["a"],
            },
        )
        assert "error" not in result2, result2.get("error")
        assert result2.get("task_executed") is True
        assert result2.get("task_action") == "spec_task"
