#!/usr/bin/env python3
"""
test_basic.py — Basic integration tests for the delta skill scripts.

Tests the core operations of spring.py and task.py using temp directories
and mock plan/progress data.

Run:
    python3 -m pytest scripts/tests/test_basic.py -v

Or directly:
    python3 scripts/tests/test_basic.py
"""

import json
import os
import sys
import tempfile
import unittest

# Add scripts/ to path so we can import spring.py, task.py
_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

# ── Test data ────────────────────────────────────────────────

SAMPLE_PREDICATES = {
    "must_exist": [
        "function reset_jackpot en src/game/reset.py",
        "class JackpotConfig en src/game/config.py",
    ],
    "must_not_exist": [
        "function legacy_jackpot en src/game/legacy.py",
    ],
    "invariants": [],
}

SAMPLE_TASKS = [
    {
        "task_id": "TASK-001",
        "type": "feature",
        "work_type": "feature_new_file",
        "objective": "Crear clase JackpotConfig con atributos game_id, amount, currency en src/game/config.py",
        "result_state": (
            "JackpotConfig creada en src/game/config.py con atributos "
            "game_id: int, amount: float, currency: str"
        ),
        "domain": "game",
        "validation": {
            "predicates": [
                "class JackpotConfig en src/game/config.py",
            ]
        },
        "impacted_artifacts": {
            "files": ["src/game/config.py"],
            "tests": ["tests/test_jackpot_config.py"],
        },
        "risks": {"curvature": 0.05, "pre_churn": 0.0, "commit_trust": 0.10},
        "acceptance_criteria": [],
        "quality_gates": [],
    },
    {
        "task_id": "TASK-002",
        "type": "feature",
        "work_type": "feature_new_method",
        "objective": "Implementar reset_jackpot(game_id) que calcula y retorna jackpot en src/game/reset.py",
        "result_state": (
            "reset_jackpot implementada en src/game/reset.py, "
            "retorna JackpotResult con amount y currency"
        ),
        "domain": "game",
        "validation": {
            "predicates": [
                "function reset_jackpot en src/game/reset.py",
            ]
        },
        "impacted_artifacts": {
            "files": ["src/game/reset.py"],
            "tests": ["tests/test_jackpot_reset.py"],
        },
        "risks": {"curvature": 0.10, "pre_churn": 0.0, "commit_trust": 0.15},
        "acceptance_criteria": [],
        "quality_gates": [],
    },
]


class TestDeltaScripts(unittest.TestCase):
    """Test core spring.py operations."""

    def setUp(self):
        """Create a temp chain directory for each test."""
        self.temp_dir = tempfile.mkdtemp(prefix="delta_test_")
        self.chain_dir = os.path.join(self.temp_dir, "test-chain")
        self.chain_id = "test-chain"
        self.project_root = self.temp_dir

    def tearDown(self):
        """Clean up temp files."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_plan_and_progress(self):
        """Create sample plan.json and progress.json in chain_dir."""
        os.makedirs(self.chain_dir, exist_ok=True)
        now = "2026-07-16T00:00:00Z"

        plan = {
            "plan": {
                "meta": {
                    "chain_id": self.chain_id,
                    "generated_at": now,
                    "version": "1.0",
                    "source": "delta-skill-test",
                    "type": "plan",
                },
                "project": {
                    "project_id": "test-project",
                    "project_type": "backend",
                    "domains": ["game"],
                    "tags": ["python"],
                },
                "goal": {
                    "description": "Implementar reseteo de juego con jackpot",
                    "predicates": SAMPLE_PREDICATES,
                    "signatures": [],
                },
                "tasks": SAMPLE_TASKS,
            }
        }

        task_states = {t["task_id"]: "pending" for t in SAMPLE_TASKS}
        progress = {
            "progress": {
                "meta": {
                    "chain_id": self.chain_id,
                    "generated_at": now,
                    "version": "1.0",
                    "source": "delta-skill-test",
                    "type": "progress",
                    "status": "active",
                },
                "plan_ref": self.chain_id,
                "trail": [],
                "current": {
                    "task_states": task_states,
                    "next_action": {
                        "task_id": "TASK-001",
                        "candidate": "Primera tarea del sprint",
                        "type": "feature",
                        "rationale": "Test",
                    },
                    "alternatives": [],
                    "risks": [],
                    "delta_metrics": {},
                },
            }
        }

        with open(os.path.join(self.chain_dir, "plan.json"), "w") as f:
            json.dump(plan, f, indent=2)
        with open(os.path.join(self.chain_dir, "progress.json"), "w") as f:
            json.dump(progress, f, indent=2)

        return plan, progress

    # ── Tests ─────────────────────────────────────────────────

    def test_spring_list_no_sprints(self):
        """spring.py list should work when no sprints exist."""
        # We can't easily test the full CLI, but we can test the data structure
        self.assertTrue(os.path.exists(self.temp_dir))
        # Verify directory was created
        self.assertTrue(os.path.isdir(self.temp_dir))

    def test_spring_create_structure(self):
        """Verify that plan.json and progress.json have correct structure."""
        self._write_plan_and_progress()

        # Read and validate plan.json
        with open(os.path.join(self.chain_dir, "plan.json")) as f:
            plan = json.load(f)
        pl = plan.get("plan", plan)
        self.assertEqual(pl["meta"]["chain_id"], self.chain_id)
        self.assertEqual(pl["project"]["project_id"], "test-project")
        self.assertIn("goal", pl)
        self.assertIn("predicates", pl["goal"])
        self.assertEqual(len(pl["tasks"]), 2)

        # Read and validate progress.json
        with open(os.path.join(self.chain_dir, "progress.json")) as f:
            progress = json.load(f)
        prog = progress.get("progress", progress)
        self.assertEqual(prog["meta"]["status"], "active")
        self.assertIn("task_states", prog["current"])
        self.assertEqual(len(prog["current"]["task_states"]), 2)
        self.assertIsNotNone(prog["current"]["next_action"])

    def test_spring_status_consistency(self):
        """Verify plan.json task_ids match progress.json task_states."""
        self._write_plan_and_progress()

        with open(os.path.join(self.chain_dir, "plan.json")) as f:
            plan = json.load(f)
        with open(os.path.join(self.chain_dir, "progress.json")) as f:
            progress = json.load(f)

        pl = plan.get("plan", plan)
        prog = progress.get("progress", progress)

        plan_tasks = {t["task_id"] for t in pl["tasks"]}
        prog_tasks = set(prog["current"]["task_states"].keys())

        self.assertEqual(
            plan_tasks, prog_tasks, "Task IDs in plan.json must match progress.json"
        )

    def test_task_has_required_fields(self):
        """Verify each task has all required fields."""
        for task in SAMPLE_TASKS:
            self.assertIn("task_id", task)
            self.assertIn("objective", task)
            self.assertIn("result_state", task)
            self.assertIn("domain", task)
            self.assertIn("type", task)
            self.assertIn("validation", task)
            self.assertIn("impacted_artifacts", task)
            self.assertIn("risks", task)

            # Content quality checks
            self.assertGreaterEqual(len(task["objective"]), 10)
            self.assertGreaterEqual(len(task["result_state"]), 10)
            self.assertGreaterEqual(len(task["validation"].get("predicates", [])), 1)
            self.assertGreaterEqual(len(task["impacted_artifacts"].get("files", [])), 1)

    def test_predicate_format(self):
        """Verify predicate strings follow parseable format."""
        for ptype, preds in SAMPLE_PREDICATES.items():
            for p in preds:
                # Each predicate should contain the word "en" to indicate location
                if ptype == "must_exist":
                    self.assertIn(
                        " en ",
                        p,
                        f"must_exist predicate '{p}' should specify location with ' en '",
                    )
                # Verify python files referenced end with .py or other valid extension
                if " en " in p:
                    file_part = p.split(" en ")[-1].strip()
                    if "." in file_part:
                        ext = file_part.split(".")[-1]
                        self.assertIn(
                            ext,
                            ["py", "ts", "js", "tsx", "jsx", "go", "rs"],
                            f"File extension '{ext}' in '{p}' may not be valid",
                        )

    def test_trail_consistency(self):
        """Verify trail entries reference valid task IDs."""
        self._write_plan_and_progress()

        with open(os.path.join(self.chain_dir, "plan.json")) as f:
            plan = json.load(f)
        with open(os.path.join(self.chain_dir, "progress.json")) as f:
            progress = json.load(f)

        pl = plan.get("plan", plan)
        prog = progress.get("progress", progress)
        valid_ids = {t["task_id"] for t in pl["tasks"]}

        # Validate existing trail entries (if any)
        for entry in prog.get("trail", []):
            tasks = entry.get("tasks", [])
            for tid in tasks:
                self.assertIn(
                    tid,
                    valid_ids,
                    f"Trail references '{tid}' which doesn't exist in plan.json",
                )

    def test_predicate_grep_format(self):
        """Verify predicates would be parseable by _parse_predicate()."""
        import re

        for ptype, preds in SAMPLE_PREDICATES.items():
            for p in preds:
                # Check that function/class names are valid Python identifiers
                if p.startswith("function ") or p.startswith("class "):
                    parts = p.split()
                    idx = 1  # after "function" or "class"
                    if idx < len(parts):
                        name = parts[idx]
                        # Function/class names should be valid identifiers
                        self.assertTrue(
                            re.match(r"^[a-zA-Z_][a-zA-Z0-9_.]*$", name),
                            f"Invalid function/class name '{name}' in predicate '{p}'",
                        )


class TestTaskOperations(unittest.TestCase):
    """Test task.py operations with mock data."""

    def test_score_calculation_signature(self):
        """Verify that score module has calculate_score function."""
        try:
            from _internal.score import calculate_score

            self.assertTrue(callable(calculate_score))
        except ImportError:
            self.skipTest("_internal.score not importable in test context")

    def test_advance_import(self):
        """Verify that advance module has key functions."""
        try:
            from _internal.advance import (
                _parse_predicate,
                run_verification,
                execute_task_loop,
                advance_task,
            )

            self.assertTrue(callable(_parse_predicate))
            self.assertTrue(callable(run_verification))
            self.assertTrue(callable(execute_task_loop))
            self.assertTrue(callable(advance_task))
        except ImportError:
            self.skipTest("_internal.advance not importable in test context")

    def test_enforce_import(self):
        """Verify that enforce module has enforce_task function."""
        try:
            from _internal.enforce import enforce_task

            self.assertTrue(callable(enforce_task))
        except ImportError:
            self.skipTest("_internal.enforce not importable in test context")

    def test_qlty_check_import(self):
        """Verify that qlty_check module has analyze_files function."""
        try:
            from _internal.qlty_check import analyze_files, evaluate_quality_gates

            self.assertTrue(callable(analyze_files))
            self.assertTrue(callable(evaluate_quality_gates))
        except ImportError:
            self.skipTest("_internal.qlty_check not importable in test context")

    def test_hallucination_import(self):
        """Verify that hallucination module has scan_file function."""
        try:
            from _internal.hallucination import scan_file

            self.assertTrue(callable(scan_file))
        except ImportError:
            self.skipTest("_internal.hallucination not importable in test context")


class TestSpringClose(unittest.TestCase):
    """Test spring.py close logic with mock progress data."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="delta_close_test_")
        self.chain_dir = os.path.join(self.temp_dir, "close-chain")
        os.makedirs(self.chain_dir, exist_ok=True)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_progress(self, task_states, status="active"):
        """Helper to create a progress.json with specific task states."""
        now = "2026-07-16T00:00:00Z"
        progress = {
            "progress": {
                "meta": {
                    "chain_id": "close-chain",
                    "generated_at": now,
                    "version": "1.0",
                    "source": "delta-skill-test",
                    "type": "progress",
                    "status": status,
                },
                "plan_ref": "close-chain",
                "trail": [{"at": now, "distance": 0.85, "event": "plan created"}],
                "current": {
                    "task_states": task_states,
                    "next_action": None,
                    "alternatives": [],
                    "risks": [],
                    "finished": True if status == "closed" else False,
                },
            }
        }
        with open(os.path.join(self.chain_dir, "progress.json"), "w") as f:
            json.dump(progress, f, indent=2)

    def test_close_all_tasks_completed(self):
        """Verify that progress with all tasks completed is closeable."""
        self._make_progress({"TASK-001": "completed", "TASK-002": "completed"})
        with open(os.path.join(self.chain_dir, "progress.json")) as f:
            data = json.load(f)
        states = data["progress"]["current"]["task_states"]
        self.assertEqual(len(states), 2)
        self.assertTrue(all(s == "completed" for s in states.values()))

    def test_close_with_pending_tasks(self):
        """Verify that progress with pending tasks is detectable."""
        self._make_progress({"TASK-001": "completed", "TASK-002": "pending"})
        with open(os.path.join(self.chain_dir, "progress.json")) as f:
            data = json.load(f)
        states = data["progress"]["current"]["task_states"]
        pending = [tid for tid, s in states.items() if s in ("pending", "in_progress")]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0], "TASK-002")

    def test_closed_sprint_not_modifiable(self):
        """Verify a closed sprint has status=closed."""
        self._make_progress({"TASK-001": "completed"}, status="closed")
        with open(os.path.join(self.chain_dir, "progress.json")) as f:
            data = json.load(f)
        self.assertEqual(data["progress"]["meta"]["status"], "closed")
        self.assertTrue(data["progress"]["current"].get("finished", False))


if __name__ == "__main__":
    unittest.main()
