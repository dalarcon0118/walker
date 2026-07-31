#!/usr/bin/env python3
"""
test_error_strategies.py — Tests for on_error strategies by error type (TASK-009).

Verifica:
  1. Error taxonomy: ValidationError, ExecutionError, StateError, etc.
  2. classify_error() mapea excepcion a ErrorType
  3. DEFAULT_STRATEGIES por tipo de error
  4. Estrategias: retry, compensate, escalate, block+message, log+continue, on_failure
  5. Sin config, uso de estrategia default

Run:
    python3 -m pytest scripts/tests/v3/test_error_strategies.py -v
"""

import os
import sys
import tempfile
import unittest

_SCRIPT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from _walker.workflow_engine.domain.errors import (
    ErrorType,
    ErrorStrategy,
    classify_error,
    DEFAULT_STRATEGIES,
    WorkflowEngineError,
    ValidationError,
    TransitionBlockedError,
    NodeNotFoundError,
    SubStateNotFoundError,
    AutoAdvanceError,
)
from _walker.workflow_engine.domain.models import (
    RetryPolicy,
    SubState,
    ExecutionType,
)


class TestErrorClassification(unittest.TestCase):
    """Tests for error taxonomy and classification."""

    def test_validation_error_classification(self):
        """Verify ValidationError -> ErrorType.VALIDATION."""
        err = ValidationError("Campo faltante")
        self.assertEqual(classify_error(err), ErrorType.VALIDATION)

    def test_transition_error_classification(self):
        """Verify TransitionBlockedError -> ErrorType.TRANSITION."""
        err = TransitionBlockedError("Precondicion no cumplida")
        self.assertEqual(classify_error(err), ErrorType.TRANSITION)

    def test_state_error_classification(self):
        """Verify NodeNotFoundError -> ErrorType.STATE."""
        err = NodeNotFoundError("SPRINT")
        self.assertEqual(classify_error(err), ErrorType.STATE)

    def test_state_error_substate(self):
        """Verify SubStateNotFoundError -> ErrorType.STATE."""
        err = SubStateNotFoundError("SPRINT", "INIT")
        self.assertEqual(classify_error(err), ErrorType.STATE)

    def test_execution_error_classification(self):
        """Verify AutoAdvanceError -> ErrorType.EXECUTION."""
        err = AutoAdvanceError("IMPLEMENT", "WORK", 1, "Script failed")
        self.assertEqual(classify_error(err), ErrorType.EXECUTION)

    def test_generic_engine_error_classification(self):
        """Verify generic WorkflowEngineError -> ErrorType.EXECUTION."""
        err = WorkflowEngineError("Generic error")
        self.assertEqual(classify_error(err), ErrorType.EXECUTION)

    def test_unknown_error_classification(self):
        """Verify unknown Exception -> ErrorType.UNKNOWN."""
        err = RuntimeError("Something went wrong")
        self.assertEqual(classify_error(err), ErrorType.UNKNOWN)

    def test_value_error_classification(self):
        """Verify ValueError -> ErrorType.UNKNOWN."""
        err = ValueError("Invalid value")
        self.assertEqual(classify_error(err), ErrorType.UNKNOWN)

    # ── ErrorType enum ─────────────────────────────────────────

    def test_all_error_types_exist(self):
        """Verify all error type strings are defined."""
        expected = {
            "validation",
            "execution",
            "transition",
            "state",
            "session",
            "infrastructure",
            "unknown",
        }
        actual = {e.value for e in ErrorType}
        self.assertEqual(actual, expected)

    def test_all_strategies_exist(self):
        """Verify all strategy strings are defined."""
        expected = {
            "retry",
            "retry_2x",
            "compensate",
            "escalate",
            "block+message",
            "log+continue",
            "on_failure",
        }
        actual = {s.value for s in ErrorStrategy}
        self.assertEqual(actual, expected)

    # ── DEFAULT_STRATEGIES ─────────────────────────────────────

    def test_default_validation_strategy(self):
        """Verify default for validation is block+message."""
        self.assertEqual(
            DEFAULT_STRATEGIES[ErrorType.VALIDATION],
            ErrorStrategy.BLOCK_MESSAGE,
        )

    def test_default_execution_strategy(self):
        """Verify default for execution is retry."""
        self.assertEqual(
            DEFAULT_STRATEGIES[ErrorType.EXECUTION],
            ErrorStrategy.RETRY,
        )

    def test_default_transition_strategy(self):
        """Verify default for transition is block+message."""
        self.assertEqual(
            DEFAULT_STRATEGIES[ErrorType.TRANSITION],
            ErrorStrategy.BLOCK_MESSAGE,
        )

    def test_default_state_strategy(self):
        """Verify default for state is escalate."""
        self.assertEqual(
            DEFAULT_STRATEGIES[ErrorType.STATE],
            ErrorStrategy.ESCALATE,
        )

    def test_default_infrastructure_strategy(self):
        """Verify default for infrastructure is retry_2x."""
        self.assertEqual(
            DEFAULT_STRATEGIES[ErrorType.INFRASTRUCTURE],
            ErrorStrategy.RETRY_2X,
        )

    def test_default_unknown_strategy(self):
        """Verify default for unknown is retry."""
        self.assertEqual(
            DEFAULT_STRATEGIES[ErrorType.UNKNOWN],
            ErrorStrategy.RETRY,
        )

    # ── RetryPolicy ────────────────────────────────────────────

    def test_retry_policy_defaults(self):
        """Verify RetryPolicy default values."""
        policy = RetryPolicy()
        self.assertEqual(policy.max_attempts, 3)
        self.assertEqual(policy.backoff, "exponential")
        self.assertEqual(policy.initial_delay_ms, 1000)
        self.assertIsNone(policy.on_error)

    def test_retry_policy_custom(self):
        """Verify RetryPolicy custom configuration."""
        policy = RetryPolicy(
            max_attempts=5,
            backoff="linear",
            initial_delay_ms=500,
            on_error={"validation": "block+message", "execution": "retry"},
        )
        self.assertEqual(policy.max_attempts, 5)
        self.assertEqual(policy.on_error["validation"], "block+message")

    def test_retry_policy_frozen(self):
        """Verify RetryPolicy is immutable (frozen dataclass)."""
        policy = RetryPolicy(max_attempts=3)
        with self.assertRaises(Exception):
            policy.max_attempts = 5

    # ── Error message and context ──────────────────────────────

    def test_error_has_message(self):
        """Verify all error types have a message."""
        err = ValidationError("test error")
        self.assertIn("test error", str(err))

    def test_error_has_context(self):
        """Verify errors carry context dict."""
        err = NodeNotFoundError("TEST_NODE")
        self.assertEqual(err.context.get("node_id"), "TEST_NODE")

    def test_transition_blocked_with_details(self):
        """Verify TransitionBlockedError carries details."""
        details = ["Sub-state incomplete: falta decision", "Node SPRINT not ready"]
        err = TransitionBlockedError("Preconditions not met", details)
        self.assertEqual(len(err.context.get("details", [])), 2)

    def test_auto_advance_error_with_exit_code(self):
        """Verify AutoAdvanceError carries exit code."""
        err = AutoAdvanceError("IMPLEMENT", "WORK", 1, "Script failed with error")
        self.assertEqual(err.context["exit_code"], 1)
        self.assertEqual(err.context["sub_key"], "WORK")


class TestErrorStrategies(unittest.TestCase):
    """Tests for error strategy resolution and application."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="delta_errors_")
        self.chain_dir = os.path.join(self.temp_dir, "test-sprint")
        os.makedirs(self.chain_dir, exist_ok=True)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sub_state_with_retry_config(self):
        """Verify SubState can hold retry config."""
        sub = SubState(
            key="WORK",
            execution=ExecutionType.AUTO,
            retry=RetryPolicy(
                max_attempts=5,
                on_error={"execution": "retry", "validation": "block+message"},
            ),
        )
        self.assertIsNotNone(sub.retry)
        self.assertEqual(sub.retry.max_attempts, 5)
        self.assertEqual(sub.retry.on_error["execution"], "retry")

    def test_sub_state_without_retry(self):
        """Verify SubState without retry config has None."""
        sub = SubState(key="MANUAL", execution=ExecutionType.MANUAL)
        self.assertIsNone(sub.retry)

    def test_on_failure_loop_flag(self):
        """Verify on_failure_loop flag in SubState."""
        sub = SubState(
            key="CODE_REVIEW", execution=ExecutionType.AUTO, on_failure_loop=True
        )
        self.assertTrue(sub.on_failure_loop)

    def test_automation_mode_check(self):
        """Verify is_auto() identifies auto and bootstrap execution types."""
        auto = SubState(key="AUTO", execution=ExecutionType.AUTO)
        bootstrap = SubState(key="BOOT", execution=ExecutionType.BOOTSTRAP)
        manual = SubState(key="MAN", execution=ExecutionType.MANUAL)

        self.assertTrue(auto.is_auto())
        self.assertTrue(bootstrap.is_auto())
        self.assertFalse(manual.is_auto())

    def test_needs_input_check(self):
        """Verify needs_input() for llm_task/user_input with required_fields."""
        llm = SubState(
            key="ANALYZE", execution=ExecutionType.LLM_TASK, required_fields=["q1"]
        )
        user = SubState(
            key="REVIEW",
            execution=ExecutionType.USER_INPUT,
            required_fields=["decision"],
        )
        empty = SubState(
            key="EMPTY", execution=ExecutionType.LLM_TASK
        )  # No required_fields

        self.assertTrue(llm.needs_input())
        self.assertTrue(user.needs_input())
        self.assertFalse(empty.needs_input())

    def test_backward_compat_mode_property(self):
        """Verify mode property works as backward-compat alias for execution."""
        sub = SubState(key="TEST", execution=ExecutionType.AUTO)
        self.assertEqual(sub.mode, ExecutionType.AUTO)

        # Setting mode updates execution
        sub.mode = ExecutionType.MANUAL
        self.assertEqual(sub.execution, ExecutionType.MANUAL)


if __name__ == "__main__":
    unittest.main()
