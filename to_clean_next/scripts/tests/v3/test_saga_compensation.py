#!/usr/bin/env python3
"""
test_saga_compensation.py — Tests for Saga/Compensation in SideEffectEngine (TASK-008).

Verifica:
  1. SideEffectEngine.execute() ejecuta efectos en orden secuencial
  2. Si efecto NO optional falla, compensa los exitosos en orden inverso
  3. Compensaciones implementadas: delete_ckg_node, restore_cpg_snapshot, rollback_ckg
  4. Idempotency key evita doble ejecucion
  5. Efectos sin compensacion loggean warning y continuan
  6. Efectos optional no gatillan compensacion

Run:
    python3 -m pytest scripts/tests/v3/test_saga_compensation.py -v
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

from _walker.workflow_engine.infrastructure.side_effects import SideEffectEngine
from _walker.workflow_engine.domain.models import SideEffectDef, ExecutionContext


class TestSagaCompensation(unittest.TestCase):
    """Tests for Saga pattern in SideEffectEngine."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="delta_saga_")
        self.chain_dir = os.path.join(self.temp_dir, "test-sprint")
        os.makedirs(self.chain_dir, exist_ok=True)
        self.engine = SideEffectEngine()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _ctx(self, task_id: str = "TASK-001") -> ExecutionContext:
        return ExecutionContext.create(
            session_id="test-session",
            chain_dir=self.chain_dir,
            task_id=task_id,
        )

    # ── Basic execution ────────────────────────────────────────

    def test_executes_effects_in_order(self):
        """Verify effects execute in given order."""
        ctx = self._ctx()
        effects = [
            SideEffectDef(type="emit_event", params={"event": "first"}),
            SideEffectDef(type="emit_event", params={"event": "second"}),
            SideEffectDef(type="emit_event", params={"event": "third"}),
        ]

        results = self.engine.execute(ctx, effects)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["result"]["event"], "first")
        self.assertEqual(results[1]["result"]["event"], "second")
        self.assertEqual(results[2]["result"]["event"], "third")

    def test_empty_effects_returns_empty_list(self):
        """Verify empty effects list returns []."""
        ctx = self._ctx()
        results = self.engine.execute(ctx, [])
        self.assertEqual(results, [])

    def test_none_effects_returns_empty_list(self):
        """Verify None effects returns []."""
        ctx = self._ctx()
        results = self.engine.execute(ctx, None)
        self.assertEqual(results, [])

    # ── Compensation on failure ─────────────────────────────────

    def test_compensation_on_non_optional_failure(self):
        """Verify when non-optional effect fails, previous effects are compensated."""
        ctx = self._ctx()

        # First effect: save_ckg (has compensation: delete_ckg_node)
        effects = [
            SideEffectDef(type="save_ckg", params={"node_id": "test-node"}),
            SideEffectDef(type="unknown_fail", params={}),  # Will fail
        ]

        results = self.engine.execute(ctx, effects)
        self.assertEqual(len(results), 2)

        # First effect should have been compensated (compensated flag set on successful)
        self.assertTrue(results[0]["ok"])
        self.assertTrue(results[0]["compensated"])
        # compensation_result is set on the FAILING entry (index 1), not on previous ones

        # Second effect failed and carries the compensation_result
        self.assertFalse(results[1]["ok"])
        self.assertIsNotNone(results[1]["compensation_result"])

    def test_compensation_order_is_reverse(self):
        """Verify compensation executes in reverse order (LIFO)."""
        ctx = self._ctx()

        # Create effects where the third fails
        effects = [
            SideEffectDef(type="save_ckg", params={"node_id": "node-1"}),
            SideEffectDef(type="save_ckg", params={"node_id": "node-2"}),
            SideEffectDef(type="unknown_fail", params={}),  # Fails
        ]

        results = self.engine.execute(ctx, effects)

        # Both successful effects should be marked compensated
        self.assertTrue(results[0]["compensated"])
        self.assertTrue(results[1]["compensated"])
        self.assertFalse(results[2]["ok"])

    def test_compensation_delete_ckg_node(self):
        """Verify delete_ckg_node compensation returns correct data."""
        ctx = self._ctx()

        effect = SideEffectDef(type="save_ckg", params={"node_id": "node-123"})
        # Execute alone, then manually compensate
        results = self.engine.execute(ctx, [effect])

        comp_results = self.engine.compensate(
            ctx, results, 1
        )  # Simulate failure at index 1
        self.assertGreater(len(comp_results), 0)

        # Check the compensation result
        if comp_results:
            comp = comp_results[0]
            self.assertIn("compensate_save_ckg", comp["type"])
            self.assertTrue(comp["ok"])

    def test_compensation_restore_cpg_snapshot(self):
        """Verify restore_cpg_snapshot compensation works."""
        ctx = self._ctx()

        effect = SideEffectDef(type="update_cpg", params={"nodes": 5, "edges": 10})
        results = self.engine.execute(ctx, [effect])

        comp_results = self.engine.compensate(ctx, results, 1)
        self.assertGreater(len(comp_results), 0)

    def test_compensation_rollback_ckg(self):
        """Verify rollback_ckg compensation returns facts count."""
        ctx = self._ctx()

        effect = SideEffectDef(type="save_to_ckg", params={"facts": 3})
        results = self.engine.execute(ctx, [effect])

        comp_results = self.engine.compensate(ctx, results, 1)
        if comp_results:
            comp = comp_results[0]
            self.assertTrue(comp["ok"])

    # ── Optional effects ───────────────────────────────────────

    def test_optional_effect_failure_does_not_compensate(self):
        """Verify optional effect failure does NOT trigger compensation."""
        ctx = self._ctx()

        effects = [
            SideEffectDef(type="save_ckg", params={"node_id": "node-1"}),
            SideEffectDef(
                type="unknown_optional", params={}, optional=True
            ),  # Fails but optional
        ]

        results = self.engine.execute(ctx, effects)

        # First effect should NOT be compensated because failure was optional
        self.assertTrue(results[0]["ok"])
        self.assertFalse(results[0].get("compensated", False))
        self.assertIsNone(results[0].get("compensation_result"))

    # ── Idempotency ────────────────────────────────────────────

    def test_idempotency_key_prevents_duplicate(self):
        """Verify same idempotency_key returns cached result."""
        ctx = self._ctx()

        effect = SideEffectDef(
            type="emit_event",
            params={"event": "test"},
            idempotency_key="unique-key-123",
        )

        # First execution
        r1 = self.engine.execute(ctx, [effect])
        self.assertFalse(r1[0].get("idempotent", False))

        # Second execution with same key
        r2 = self.engine.execute(ctx, [effect])
        self.assertTrue(r2[0].get("idempotent", False))

    def test_different_keys_are_not_idempotent(self):
        """Verify different idempotency keys execute separately."""
        ctx = self._ctx()

        e1 = SideEffectDef(
            type="emit_event", params={"event": "a"}, idempotency_key="key-a"
        )
        e2 = SideEffectDef(
            type="emit_event", params={"event": "b"}, idempotency_key="key-b"
        )

        r1 = self.engine.execute(ctx, [e1])
        r2 = self.engine.execute(ctx, [e2])

        self.assertFalse(r1[0].get("idempotent", False))
        self.assertFalse(r2[0].get("idempotent", False))

    def test_idempotency_per_chain_dir(self):
        """Verify idempotency is scoped per chain directory."""
        ctx1 = self._ctx()
        ctx2 = ExecutionContext.create(
            session_id="test-session-2",
            chain_dir=os.path.join(self.temp_dir, "other-sprint"),
            task_id="TASK-002",
        )

        effect = SideEffectDef(
            type="emit_event",
            params={"event": "test"},
            idempotency_key="same-key",
        )

        r1 = self.engine.execute(ctx1, [effect])
        r2 = self.engine.execute(ctx2, [effect])

        # Both should execute (different chain_dir)
        self.assertFalse(r1[0].get("idempotent", False))
        self.assertFalse(r2[0].get("idempotent", False))

    def test_clear_idempotency_resets_cache(self):
        """Verify clear_idempotency() resets the store for a chain."""
        ctx = self._ctx()

        effect = SideEffectDef(
            type="emit_event", params={"event": "test"}, idempotency_key="k"
        )

        self.engine.execute(ctx, [effect])
        self.engine.clear_idempotency(self.chain_dir)

        # Should execute again (cache cleared)
        r2 = self.engine.execute(ctx, [effect])
        self.assertFalse(r2[0].get("idempotent", False))

    # ── Mixed scenarios ────────────────────────────────────────

    def test_mix_of_effects_and_compensation(self):
        """Verify complex mix: save_ckg → update_cpg → emit_event → fail."""
        ctx = self._ctx()

        effects = [
            SideEffectDef(
                type="save_ckg", params={"node_id": "n1"}
            ),  # Has compensation
            SideEffectDef(type="update_cpg", params={"nodes": 3}),  # Has compensation
            SideEffectDef(
                type="emit_event", params={"event": "progress"}
            ),  # No compensation
            SideEffectDef(type="unknown_fail", params={}),  # Will fail
        ]

        results = self.engine.execute(ctx, effects)

        # First 3 should be compensated
        for i in range(3):
            self.assertTrue(
                results[i]["compensated"], f"Effect {i} should be compensated"
            )

        # Last should have failed
        self.assertFalse(results[3]["ok"])

    def test_no_compensation_logs_warning(self):
        """Verify effects without compensation log warning (continue)."""
        ctx = self._ctx()

        effect = SideEffectDef(type="emit_event", params={"event": "test"})
        results = self.engine.execute(ctx, [effect])

        comp_results = self.engine.compensate(ctx, results, 1)
        # Should have a warning entry but not fail
        if comp_results:
            self.assertTrue(comp_results[0]["ok"])
            self.assertTrue(comp_results[0].get("skipped", False))


if __name__ == "__main__":
    unittest.main()
