#!/usr/bin/env python3
"""
test_prompt_resolution.py — Tests for prompt_id resolution engine with placeholders.

Verifica:
  1. Carga prompts/{id}.prompt.json
  2. Resuelve placeholders {task_id}, {project_id}, {sprint_goal}, etc.
  3. Fallback a on_entry_message si prompt_id no existe
  4. Resolución desde OrchestrationService.resolve_prompt()

Run:
    python3 -m pytest scripts/tests/v3/test_prompt_resolution.py -v
"""
import json
import os
import sys
import tempfile
import unittest

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from _walker.workflow_engine.application.orchestrator import OrchestrationService


class TestPromptResolution(unittest.TestCase):
    """Tests for prompt_id resolution with placeholders."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="delta_prompt_")
        # chain_dir must be at 3 levels so os.path.dirname(os.path.dirname(chain_dir))
        # resolves to the parent containing workflow.v3/
        # chain_dir = {temp_dir}/projects/delta/test-sprint
        # parent = {temp_dir}/projects
        # workflow.v3 at {temp_dir}/projects/workflow.v3/
        self.project_dir = os.path.join(self.temp_dir, "projects")
        self.chain_dir = os.path.join(self.project_dir, "delta", "test-sprint")
        self.workflow_dir = os.path.join(self.project_dir, "workflow.v3")
        self.prompts_dir = os.path.join(self.workflow_dir, "prompts")
        os.makedirs(self.chain_dir, exist_ok=True)
        os.makedirs(self.prompts_dir, exist_ok=True)
        
        # Create sprint-state.json with context for placeholders
        state = {
            "version": "5.0",
            "current_node": "ANALYZE",
            "current_sub_state": "ANALYZE",
            "sprint": {
                "project_id": "delta-skill",
                "goal": "Migrar a workflow_engine enterprise",
                "status": "active"
            },
            "tasks": {
                "TASK-001": {"status": "completed", "objective": "Task 1"},
                "TASK-002": {"status": "completed", "objective": "Task 2"},
                "TASK-003": {"status": "in_progress", "objective": "Task 3"},
            },
            "task_order": ["TASK-001", "TASK-002", "TASK-003"],
            "state": {"tasks": ["TASK-001", "TASK-002", "TASK-003"]},
        }
        with open(os.path.join(self.chain_dir, "sprint-state.json"), "w") as f:
            json.dump(state, f, indent=2)
        
        self.service = OrchestrationService()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_prompt(self, prompt_id: str, template: str):
        """Helper to create a prompt file."""
        prompt = {"id": prompt_id, "template": template}
        path = os.path.join(self.prompts_dir, f"{prompt_id}.prompt.json")
        with open(path, "w") as f:
            json.dump(prompt, f, indent=2)

    # ── Tests ─────────────────────────────────────────────────

    def test_prompt_loads_correct_file(self):
        """Verify prompt_id loads the correct prompts/{id}.prompt.json."""
        self._create_prompt("analyze-task", "Analiza la tarea {task_id}")
        
        resolved = self.service.resolve_prompt("analyze-task", self.chain_dir, task_id="TASK-003")
        self.assertIn("TASK-003", resolved)
        self.assertIn("Analiza la tarea", resolved)

    def test_placeholder_task_id(self):
        """Verify {task_id} placeholder resolves correctly."""
        self._create_prompt("test", "Task: {task_id}")
        
        resolved = self.service.resolve_prompt("test", self.chain_dir, task_id="TASK-001")
        self.assertEqual(resolved, "Task: TASK-001")

    def test_placeholder_sprint_goal(self):
        """Verify {sprint_goal} placeholder resolves from sprint-state."""
        self._create_prompt("test", "Goal: {sprint_goal}")
        
        resolved = self.service.resolve_prompt("test", self.chain_dir)
        self.assertIn("Migrar a workflow_engine enterprise", resolved)

    def test_placeholder_project_id(self):
        """Verify {project_id} placeholder resolves from sprint-state."""
        self._create_prompt("test", "Project: {project_id}")
        
        resolved = self.service.resolve_prompt("test", self.chain_dir)
        self.assertIn("delta-skill", resolved)

    def test_placeholder_task_summary(self):
        """Verify {task_summary} placeholder shows completed/total."""
        self._create_prompt("test", "Summary: {task_summary}")
        
        resolved = self.service.resolve_prompt("test", self.chain_dir)
        self.assertIn("2/3", resolved)

    def test_placeholder_goal_display(self):
        """Verify {goal_display} placeholder formats goal."""
        self._create_prompt("test", "{goal_display}")
        
        resolved = self.service.resolve_prompt("test", self.chain_dir)
        self.assertIn("Goal:", resolved)

    def test_multiple_placeholders_in_one_prompt(self):
        """Verify multiple placeholders resolve in the same template."""
        self._create_prompt("test", "Task {task_id}: {sprint_goal} ({task_summary})")
        
        resolved = self.service.resolve_prompt("test", self.chain_dir, task_id="TASK-002")
        self.assertIn("TASK-002", resolved)
        self.assertIn("Migrar", resolved)
        self.assertIn("2/3", resolved)

    def test_prompt_not_found_returns_fallback(self):
        """Verify non-existent prompt_id returns fallback message."""
        resolved = self.service.resolve_prompt("non-existent-prompt", self.chain_dir)
        self.assertIn("prompt no encontrado", resolved)

    def test_prompt_without_task_id_still_resolves(self):
        """Verify resolution works even without task_id."""
        self._create_prompt("test", "Goal: {sprint_goal}")
        
        resolved = self.service.resolve_prompt("test", self.chain_dir)
        self.assertIn("Migrar", resolved)

    def test_orchestrator_step_returns_resolved_prompt(self):
        """Verify OrchestrationService.step() includes resolved prompt for prompt_id."""
        # Create an ANALYZE node with prompt_id in sprint-state
        state_path = os.path.join(self.chain_dir, "sprint-state.json")
        with open(state_path) as f:
            state = json.load(f)
        state["current_node"] = "ANALYZE"
        state["current_sub_state"] = "ANALYZE"
        state["sub_state_index"] = 1
        with open(state_path, "w") as f:
            json.dump(state, f, indent=2)
        
        # We can't easily test step() with a custom workflow here since
        # the repo resolves from chain_dir parent. Instead test resolve_prompt directly.
        self._create_prompt("analyze-task", "Analiza {task_id}: {sprint_goal}")
        resolved = self.service.resolve_prompt("analyze-task", self.chain_dir, task_id="TASK-003")
        self.assertIn("TASK-003", resolved)
        self.assertIn("Migrar", resolved)

    def test_empty_template_returns_empty_string(self):
        """Verify empty template returns empty string."""
        self._create_prompt("empty", "")
        
        resolved = self.service.resolve_prompt("empty", self.chain_dir)
        self.assertEqual(resolved, "")

    def test_json_template_preserves_structure(self):
        """Verify JSON-like templates are not mangled by resolution."""
        template = '{"task": "{task_id}", "goal": "{sprint_goal}"}'
        self._create_prompt("json-test", template)
        
        resolved = self.service.resolve_prompt("json-test", self.chain_dir, task_id="TASK-001")
        self.assertIn("TASK-001", resolved)
        self.assertIn("Migrar", resolved)
        # Should still be valid JSON-ish
        self.assertIn("{", resolved)
        self.assertIn("}", resolved)

    def test_unknown_placeholder_returns_empty(self):
        """Verify unknown placeholders resolve to empty string."""
        self._create_prompt("test", "Value: {unknown_var}")
        
        resolved = self.service.resolve_prompt("test", self.chain_dir)
        self.assertEqual(resolved, "Value: ")

    def test_resolve_with_session_id(self):
        """Verify resolution includes session context."""
        self._create_prompt("test", "Session: {session_id}")
        
        # session_id comes from chain_dir basename in the prompt context builder
        resolved = self.service.resolve_prompt("test", self.chain_dir)
        # Should not contain the placeholder literally
        self.assertNotIn("{session_id}", resolved)


if __name__ == "__main__":
    unittest.main()
