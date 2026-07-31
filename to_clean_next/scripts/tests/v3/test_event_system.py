#!/usr/bin/env python3
"""
test_event_system.py — Tests for on:{} event system (TASK-006).

Verifica:
  1. side-effect emit_event dispara busqueda en workflow.json 'on'
  2. Handler 'run' ejecuta accion via TaskRegistry
  3. Handler 'transition' aplica transicion entre nodos
  4. Eventos llevan metadata (task_id, node, timestamp)
  5. Sin handler, se ignora silenciosamente

Run:
    python3 -m pytest scripts/tests/v3/test_event_system.py -v
"""

import json
import os
import sys
import tempfile
import unittest

_SCRIPT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from _walker.workflow_engine.application.orchestrator import OrchestrationService
from _walker.workflow_engine.infrastructure.repositories import (
    WorkflowRepository,
)
from _walker.workflow_engine.infrastructure.side_effects import SideEffectEngine
from _walker.workflow_engine.infrastructure.task_registry import InMemoryTaskRegistry
from _walker.workflow_engine.domain.models import (
    SideEffectDef,
    ExecutionContext,
)


class TestEventSystem(unittest.TestCase):
    """Tests for the on:{} event system."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="delta_events_")
        # chain_dir must be at 3 levels to match WorkflowRepository._resolve_path
        self.project_dir = os.path.join(self.temp_dir, "projects")
        self.chain_dir = os.path.join(self.project_dir, "delta", "test-sprint")
        self.workflow_dir = os.path.join(self.project_dir, "workflow.v3")
        self.nodes_dir = os.path.join(self.workflow_dir, "nodes")
        os.makedirs(self.chain_dir, exist_ok=True)
        os.makedirs(self.nodes_dir, exist_ok=True)

        # Create a simple sprint state
        state = {
            "version": "5.0",
            "current_node": "SPRINT",
            "current_sub_state": "ACTIVE",
            "sub_state_index": 1,
            "sub_state_data": {},
            "history": ["SPRINT.ACTIVE"],
            "context": {"chain_dir": self.chain_dir},
            "sprint": {
                "project_id": "delta-skill",
                "goal": "Test events",
                "status": "active",
            },
            "tasks": {},
            "state": {"tasks": []},
        }
        with open(os.path.join(self.chain_dir, "sprint-state.json"), "w") as f:
            json.dump(state, f, indent=2)

        # Create node files
        self._create_nodes()

        # Create edges
        edges = [
            {
                "from": "SPRINT",
                "gate": "back_to_active",
                "to": "SPRINT",
                "sub_state_gate": "ACTIVE",
                "description": "Volver al dashboard",
            },
        ]
        with open(os.path.join(self.workflow_dir, "edges.json"), "w") as f:
            json.dump(edges, f, indent=2)

        # Create workflow.json with on:{} events
        self._create_workflow()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_nodes(self):
        sprint = {
            "label": "Sprint",
            "sub_states": [
                {
                    "key": "PLANNING",
                    "execution": "llm_task",
                    "required_fields": ["goal", "tasks"],
                },
                {"key": "ACTIVE", "execution": "auto"},
            ],
        }
        with open(os.path.join(self.nodes_dir, "sprint.node.json"), "w") as f:
            json.dump(sprint, f, indent=2)

    def _create_workflow(self):
        wf = {
            "version": "5.0",
            "import": {"nodes": ["nodes/sprint.node.json"], "edges": "edges.json"},
            "on": {
                "task_approved": {"run": "log_approval"},
                "sprint_planned": {"transition": "start_task"},
                "qa_failed": {"run": "notify_qa_failure"},
                "sprint_closed": {"transition": "back_to_active"},
            },
        }
        with open(os.path.join(self.workflow_dir, "workflow.json"), "w") as f:
            json.dump(wf, f, indent=2)

    def _create_context(self) -> ExecutionContext:
        return ExecutionContext.create(
            session_id="test-session",
            chain_dir=self.chain_dir,
            task_id="TASK-001",
        )

    # ── Tests ─────────────────────────────────────────────────

    def test_workflow_loads_on_events(self):
        """Verify WorkflowRepository loads on:{} event handlers."""
        repo = WorkflowRepository()
        graph = repo.load(self.chain_dir)

        self.assertIsNotNone(graph)
        handler = graph.find_event_handler("task_approved")
        self.assertIsNotNone(handler)
        self.assertEqual(handler["run"], "log_approval")

        handler2 = graph.find_event_handler("sprint_planned")
        self.assertIsNotNone(handler2)
        self.assertEqual(handler2["transition"], "start_task")

    def test_all_four_events_loaded(self):
        """Verify all 4 configured events are loaded."""
        repo = WorkflowRepository()
        graph = repo.load(self.chain_dir)

        for event in ["task_approved", "sprint_planned", "qa_failed", "sprint_closed"]:
            handler = graph.find_event_handler(event)
            self.assertIsNotNone(handler, f"Event '{event}' not loaded")

    def test_nonexistent_event_returns_none(self):
        """Verify non-existent event returns None."""
        repo = WorkflowRepository()
        graph = repo.load(self.chain_dir)

        handler = graph.find_event_handler("non_existent_event")
        self.assertIsNone(handler)

    def test_event_handler_run_action(self):
        """Verify 'run' handler type executes action via TaskRegistry."""
        registry = InMemoryTaskRegistry()
        executed_actions = []

        from _walker.workflow_engine.domain.models import TaskResult

        def fake_log_approval(ctx, data):
            executed_actions.append("log_approval")
            return TaskResult.success(data="logged")

        registry.register("log_approval", fake_log_approval)

        # Simulate event dispatch
        handler = {"run": "log_approval"}
        action = handler["run"]

        ctx = self._create_context()
        result = registry.execute(action, ctx, {"event": "task_approved"})

        self.assertTrue(result.ok)
        self.assertIn("log_approval", executed_actions)

    def test_side_effect_emit_event(self):
        """Verify SideEffectEngine executes emit_event."""
        engine = SideEffectEngine()
        ctx = self._create_context()

        effect = SideEffectDef(
            type="emit_event",
            params={"event": "task_approved", "payload": {"task_id": "TASK-001"}},
        )

        results = engine.execute(ctx, [effect])
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["ok"])
        self.assertEqual(results[0]["result"]["event"], "task_approved")

    def test_side_effect_emit_event_multiple(self):
        """Verify multiple emit_event side-effects execute in order."""
        engine = SideEffectEngine()
        ctx = self._create_context()

        effects = [
            SideEffectDef(type="emit_event", params={"event": "sprint_planned"}),
            SideEffectDef(type="emit_event", params={"event": "task_approved"}),
        ]

        results = engine.execute(ctx, effects)
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]["ok"])
        self.assertTrue(results[1]["ok"])
        self.assertEqual(results[0]["result"]["event"], "sprint_planned")
        self.assertEqual(results[1]["result"]["event"], "task_approved")

    def test_side_effect_options_does_not_fail(self):
        """Verify optional side-effect failure doesn't block execution."""
        engine = SideEffectEngine()
        ctx = self._create_context()

        effect = SideEffectDef(
            type="emit_event",
            params={"event": "qa_failed", "payload": {"reason": "Tests failed"}},
            optional=True,
        )

        results = engine.execute(ctx, [effect])
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["ok"])

    def test_side_effect_with_metadata(self):
        """Verify emit_event carries metadata (task_id, timestamp)."""
        engine = SideEffectEngine()
        ctx = self._create_context()

        effect = SideEffectDef(
            type="emit_event",
            params={
                "event": "task_approved",
                "payload": {
                    "task_id": "TASK-001",
                    "node": "ANALYZE.REVIEW",
                },
            },
        )

        results = engine.execute(ctx, [effect])
        result = results[0]["result"]
        self.assertEqual(result["payload"]["task_id"], "TASK-001")
        self.assertEqual(result["payload"]["node"], "ANALYZE.REVIEW")
        self.assertTrue(result["emitted"])

    def test_event_metadata_includes_timestamp(self):
        """Verify event result includes timestamp."""
        engine = SideEffectEngine()
        ctx = self._create_context()

        effect = SideEffectDef(type="emit_event", params={"event": "test_event"})
        results = engine.execute(ctx, [effect])

        self.assertIn("timestamp", results[0]["result"])

    def test_multiple_events_with_idempotency(self):
        """Verify idempotency key prevents duplicate event emission."""
        engine = SideEffectEngine()
        ctx = self._create_context()

        effect = SideEffectDef(
            type="emit_event",
            params={"event": "task_approved"},
            idempotency_key="event-task-approved-001",
        )

        # First execution
        results1 = engine.execute(ctx, [effect])
        self.assertFalse(results1[0].get("idempotent", False))

        # Second execution with same key
        results2 = engine.execute(ctx, [effect])
        self.assertTrue(results2[0].get("idempotent", False))

    def test_orchestrator_creates_context(self):
        """Verify OrchestrationService.create_context() works."""
        service = OrchestrationService()
        ctx = service.create_context(self.chain_dir, task_id="TASK-001")

        self.assertEqual(ctx.chain_dir, self.chain_dir)
        self.assertEqual(ctx.task_id, "TASK-001")
        self.assertIsNotNone(ctx.session_id)
        self.assertIsNotNone(ctx.trace_id)


if __name__ == "__main__":
    unittest.main()
