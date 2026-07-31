#!/usr/bin/env python3
"""
test_audit_tracing.py — Tests for audit/tracing with TraceLog (TASK-010).

Verifica:
  1. TraceLogger escribe entradas JSONL en archivo
  2. trace() escribe NODE_ENTRY, SUBMIT, TRANSITION, ERROR
  3. trace_auto() captura stdout/stderr (capture_stdout)
  4. trace_submit() captura decision/evidence (capture_decision)
  5. trace_transition() captura gate, from, to
  6. replay() lee entradas en orden cronologico
  7. Tracing configurable desde workflow.json

Run:
    python3 -m pytest scripts/tests/v3/test_audit_tracing.py -v
"""
import json
import os
import sys
import tempfile
import unittest

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from _walker.workflow_engine.infrastructure.trace_log import TraceLogger


class TestTraceLogger(unittest.TestCase):
    """Tests for TraceLogger audit/tracing."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="delta_trace_")
        self.chain_dir = os.path.join(self.temp_dir, "test-sprint")
        os.makedirs(self.chain_dir, exist_ok=True)
        self.tracer = TraceLogger()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _trace_path(self) -> str:
        return os.path.join(self.chain_dir, "trace.jsonl")

    def _read_trace(self) -> list[dict]:
        path = self._trace_path()
        if not os.path.exists(path):
            return []
        entries = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    # ── Basic tracing ──────────────────────────────────────────

    def test_trace_writes_jsonl_file(self):
        """Verify trace() creates JSONL file with entry."""
        self.tracer.trace(self.chain_dir, "NODE_ENTRY", {"node": "SPRINT"})
        
        self.assertTrue(os.path.exists(self._trace_path()))
        entries = self._read_trace()
        self.assertEqual(len(entries), 1)

    def test_trace_entry_has_timestamp(self):
        """Verify trace entry includes timestamp."""
        self.tracer.trace(self.chain_dir, "NODE_ENTRY", {"node": "SPRINT"})
        
        entries = self._read_trace()
        self.assertIn("timestamp", entries[0])

    def test_trace_entry_has_event_type(self):
        """Verify trace entry includes event_type."""
        self.tracer.trace(self.chain_dir, "NODE_ENTRY", {"node": "SPRINT"})
        
        entries = self._read_trace()
        self.assertEqual(entries[0]["event_type"], "NODE_ENTRY")

    def test_trace_entry_has_data(self):
        """Verify trace entry includes data dict."""
        self.tracer.trace(self.chain_dir, "NODE_ENTRY", {"node": "SPRINT", "index": 0})
        
        entries = self._read_trace()
        self.assertEqual(entries[0]["data"]["node"], "SPRINT")
        self.assertEqual(entries[0]["data"]["index"], 0)

    def test_multiple_entries_are_appended(self):
        """Verify multiple trace calls append sequentially."""
        self.tracer.trace(self.chain_dir, "NODE_ENTRY", {"node": "SPRINT"})
        self.tracer.trace(self.chain_dir, "SUBMIT", {"field": "decision"})
        self.tracer.trace(self.chain_dir, "TRANSITION", {"gate": "start_task"})
        
        entries = self._read_trace()
        self.assertEqual(len(entries), 3)

    # ── event_type variants ────────────────────────────────────

    def test_trace_auto_captures_stdout(self):
        """Verify trace_auto() captures stdout/stderr/exit_code."""
        self.tracer.trace_auto(
            self.chain_dir, "GATHER",
            stdout="Context loaded successfully",
            stderr="",
            exit_code=0,
            task_id="TASK-001",
        )
        
        entries = self._read_trace()
        self.assertEqual(entries[0]["event_type"], "AUTO_EXECUTE")
        self.assertIn("stdout", entries[0]["data"])
        self.assertIn("exit_code", entries[0]["data"])
        self.assertEqual(entries[0]["data"]["exit_code"], 0)
        self.assertEqual(entries[0]["task_id"], "TASK-001")

    def test_trace_auto_with_stderr(self):
        """Verify trace_auto() captures stderr on failure."""
        self.tracer.trace_auto(
            self.chain_dir, "WORK",
            stdout="",
            stderr="Script failed with error",
            exit_code=1,
        )
        
        entries = self._read_trace()
        self.assertEqual(entries[0]["data"]["exit_code"], 1)
        self.assertIn("Script failed", entries[0]["data"]["stderr"])

    def test_trace_submit_captures_decision(self):
        """Verify trace_submit() captures decision and evidence."""
        self.tracer.trace_submit(
            self.chain_dir, "ANALYZE.REVIEW",
            decision="approve",
            evidence="All criteria met",
            task_id="TASK-001",
        )
        
        entries = self._read_trace()
        self.assertEqual(entries[0]["event_type"], "SUBMIT")
        self.assertEqual(entries[0]["data"]["decision"], "approve")
        self.assertEqual(entries[0]["data"]["evidence"], "All criteria met")
        self.assertEqual(entries[0]["task_id"], "TASK-001")

    def test_trace_submit_with_reject(self):
        """Verify trace_submit() captures reject decision."""
        self.tracer.trace_submit(
            self.chain_dir, "IMPLEMENT.REPORT_GATE",
            decision="reject",
            evidence="Quality gates failed",
        )
        
        entries = self._read_trace()
        self.assertEqual(entries[0]["data"]["decision"], "reject")

    def test_trace_transition(self):
        """Verify trace_transition() captures gate, from, to."""
        self.tracer.trace_transition(
            self.chain_dir, "start_task",
            from_node="SPRINT.ACTIVE",
            to_node="ANALYZE.GATHER",
            task_id="TASK-001",
        )
        
        entries = self._read_trace()
        self.assertEqual(entries[0]["event_type"], "TRANSITION")
        self.assertEqual(entries[0]["data"]["gate"], "start_task")
        self.assertEqual(entries[0]["data"]["from"], "SPRINT.ACTIVE")
        self.assertEqual(entries[0]["data"]["to"], "ANALYZE.GATHER")

    def test_trace_error(self):
        """Verify trace_error() captures error type and message."""
        self.tracer.trace_error(
            self.chain_dir,
            error_type="VALIDATION",
            message="Campo requerido faltante: decision",
        )
        
        entries = self._read_trace()
        self.assertEqual(entries[0]["event_type"], "ERROR")
        self.assertEqual(entries[0]["data"]["error_type"], "VALIDATION")

    # ── Replay ─────────────────────────────────────────────────

    def test_replay_returns_entries_in_order(self):
        """Verify replay() returns entries in chronological order."""
        self.tracer.trace(self.chain_dir, "FIRST", {})
        self.tracer.trace(self.chain_dir, "SECOND", {})
        self.tracer.trace(self.chain_dir, "THIRD", {})
        
        entries = self.tracer.replay(self.chain_dir)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["event_type"], "FIRST")
        self.assertEqual(entries[1]["event_type"], "SECOND")
        self.assertEqual(entries[2]["event_type"], "THIRD")

    def test_replay_empty(self):
        """Verify replay() returns [] when no trace file exists."""
        entries = self.tracer.replay(self.chain_dir)
        self.assertEqual(entries, [])

    def test_replay_after_clear(self):
        """Verify replay returns empty after deleting trace file."""
        self.tracer.trace(self.chain_dir, "TEST", {})
        self.assertTrue(len(self.tracer.replay(self.chain_dir)) > 0)
        
        os.remove(self._trace_path())
        entries = self.tracer.replay(self.chain_dir)
        self.assertEqual(entries, [])

    # ── Custom trace_dir ───────────────────────────────────────

    def test_custom_trace_dir(self):
        """Verify custom trace_dir writes to specified location."""
        custom_dir = os.path.join(self.temp_dir, "custom-traces")
        os.makedirs(custom_dir, exist_ok=True)
        
        tracer = TraceLogger(trace_dir=custom_dir)
        tracer.trace(self.chain_dir, "NODE_ENTRY", {"node": "SPRINT"})
        
        # Trace file should be in custom_dir
        trace_name = os.path.basename(self.chain_dir)
        trace_path = os.path.join(custom_dir, f"{trace_name}.jsonl")
        self.assertTrue(os.path.exists(trace_path))

    # ── Tracing config ─────────────────────────────────────────

    def test_get_tracing_config_default(self):
        """Verify get_tracing_config returns defaults without workflow.json."""
        config = self.tracer.get_tracing_config(self.chain_dir)
        self.assertEqual(config["level"], "full")
        self.assertEqual(config["track"], [])

    def test_get_tracing_config_from_workflow(self):
        """Verify get_tracing_config reads from workflow.json."""
        # Create minimal workflow.json with tracing config
        wf_dir = self.chain_dir
        wf = {
            "tracing": {
                "level": "minimal",
                "track": ["NODE_ENTRY", "TRANSITION"]
            }
        }
        with open(os.path.join(wf_dir, "workflow.json"), "w") as f:
            json.dump(wf, f, indent=2)
        
        config = self.tracer.get_tracing_config(wf_dir)
        self.assertEqual(config["level"], "minimal")
        self.assertEqual(config["track"], ["NODE_ENTRY", "TRANSITION"])

    def test_should_track_all_when_no_config(self):
        """Verify should_track returns True when level=full and track empty."""
        wf_dir = self.chain_dir
        wf = {"tracing": {"level": "full", "track": []}}
        with open(os.path.join(wf_dir, "workflow.json"), "w") as f:
            json.dump(wf, f, indent=2)
        
        self.assertTrue(self.tracer.should_track("NODE_ENTRY", wf_dir))

    def test_should_track_filtered(self):
        """Verify should_track filters by track list."""
        wf_dir = self.chain_dir
        wf = {"tracing": {"level": "tracked", "track": ["NODE_ENTRY"]}}
        with open(os.path.join(wf_dir, "workflow.json"), "w") as f:
            json.dump(wf, f, indent=2)
        
        self.assertTrue(self.tracer.should_track("NODE_ENTRY", wf_dir))
        self.assertFalse(self.tracer.should_track("SUBMIT", wf_dir))

    def test_should_track_no_workflow(self):
        """Verify should_track returns True without workflow.json."""
        self.assertTrue(self.tracer.should_track("NODE_ENTRY", self.chain_dir))

    # ── Session and task_id ────────────────────────────────────

    def test_trace_with_session_id(self):
        """Verify trace entry can include session_id."""
        self.tracer.trace(
            self.chain_dir, "NODE_ENTRY", {"node": "SPRINT"},
            session_id="sess-abc123",
        )
        
        entries = self._read_trace()
        self.assertEqual(entries[0]["session_id"], "sess-abc123")

    def test_trace_with_task_id(self):
        """Verify trace entry can include task_id."""
        self.tracer.trace(
            self.chain_dir, "SUBMIT", {"field": "decision"},
            task_id="TASK-005",
        )
        
        entries = self._read_trace()
        self.assertEqual(entries[0]["task_id"], "TASK-005")

    # ── Flush ──────────────────────────────────────────────────

    def test_flush_clears_buffer(self):
        """Verify flush() clears internal buffer (no-op for file writer)."""
        self.tracer.trace(self.chain_dir, "TEST", {})
        self.tracer.flush()
        # Data should still be on disk (flush is no-op for direct-write logger)
        entries = self._read_trace()
        self.assertEqual(len(entries), 1)


if __name__ == "__main__":
    unittest.main()
