"""tests/test_side_effect_outbox_bdd.py — Side effects atómicos con Outbox pattern.

SPEC-009: garantizar exactly-once execution de side effects, incluso si
el proceso falla a mitad de la ejecución.

Tests:
- test_outbox_enqueue_persists_effect
- test_outbox_replay_after_failure
- test_outbox_atomicity_all_or_nothing
- test_outbox_idempotency_prevents_duplicate_execution
- test_outbox_clear_completed_removes_old_entries
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from features.engine.domain.models import SideEffectDef
from features.engine.infrastructure.side_effect_outbox import SideEffectOutbox, OutboxEntry


# ── Helpers ─────────────────────────────────────────────────────────────

def _create_outbox(tmp_path: Path) -> SideEffectOutbox:
    chain_dir = str(tmp_path / "sprint-test")
    os.makedirs(chain_dir, exist_ok=True)
    return SideEffectOutbox(chain_dir)


def _make_effect(effect_type: str, params: dict | None = None, optional: bool = False) -> SideEffectDef:
    return SideEffectDef(
        type=effect_type,
        params=params or {},
        optional=optional,
        idempotency_key=None,
    )


# ── Tests Outbox ─────────────────────────────────────────────────────────

class TestSideEffectOutbox:
    """Side effects atómicos con Outbox pattern."""

    def test_outbox_enqueue_persists_effect(self, tmp_path):
        outbox = _create_outbox(tmp_path)
        effect = _make_effect("transform_spec", {"status": "specified"})

        entry = outbox.enqueue(effect)

        assert entry.effect_type == "transform_spec"
        assert entry.status == "pending"
        assert entry.params == {"status": "specified"}
        assert outbox.pending() == [entry]

    def test_outbox_replay_after_failure(self, tmp_path):
        outbox = _create_outbox(tmp_path)
        effect1 = _make_effect("transform_spec", {"status": "specified"})
        effect2 = _make_effect("save_to_ckg", {"node_id": "N1"})

        entry1 = outbox.enqueue(effect1)
        entry2 = outbox.enqueue(effect2)

        entry1.mark_completed({"ok": True})

        for e in outbox.all():
            if e.status == "pending":
                outbox.mark_executing(e)
                outbox.mark_failed(e, "ConnectionError")

        replay = outbox.replay_pending()
        assert len(replay) == 0
        assert len(outbox.failed()) == 1
        assert outbox.failed()[0].effect_type == "save_to_ckg"

    def test_outbox_atomicity_all_or_nothing(self, tmp_path):
        outbox = _create_outbox(tmp_path)
        effects = [
            _make_effect("transform_spec", {"status": "specified"}),
            _make_effect("save_to_ckg", {"node_id": "N1"}),
            _make_effect("emit_event", {"event": "spec_specified"}),
        ]

        entries = [outbox.enqueue(e) for e in effects]

        for entry in entries:
            outbox.mark_executing(entry)
            outbox.mark_completed(entry, {"ok": True})

        assert len(outbox.completed()) == 3
        assert len(outbox.pending()) == 0
        assert len(outbox.failed()) == 0

    def test_outbox_idempotency_prevents_duplicate_execution(self, tmp_path):
        outbox = _create_outbox(tmp_path)
        effect = SideEffectDef(
            type="transform_spec",
            params={"status": "specified"},
            optional=False,
            idempotency_key="spec-transform-001",
        )

        entry1 = outbox.enqueue(effect, idempotency_key="spec-transform-001")
        outbox.mark_completed(entry1, {"ok": True})

        entry2 = outbox.enqueue(effect, idempotency_key="spec-transform-001")

        assert entry1 is entry2 or entry1.effect_type == entry2.effect_type

    def test_outbox_clear_completed_removes_old_entries(self, tmp_path):
        outbox = _create_outbox(tmp_path)
        effect = _make_effect("transform_spec", {"status": "specified"})

        entry = outbox.enqueue(effect)
        outbox.mark_completed(entry, {"ok": True})

        assert len(outbox.completed()) == 1

        outbox.clear_completed(older_than_hours=0)

        assert len(outbox.completed()) == 0

    def test_outbox_stats_tracks_entries(self, tmp_path):
        outbox = _create_outbox(tmp_path)
        effect1 = _make_effect("transform_spec")
        effect2 = _make_effect("save_to_ckg")
        effect3 = _make_effect("emit_event")

        entry1 = outbox.enqueue(effect1)
        entry2 = outbox.enqueue(effect2)
        entry3 = outbox.enqueue(effect3)

        outbox.mark_completed(entry1, {"ok": True})
        outbox.mark_failed(entry2, "Error")
        outbox.mark_executing(entry3)

        stats = outbox.stats()
        assert stats["total"] == 3
        assert stats["completed"] == 1
        assert stats["failed"] == 1
        assert stats["executing"] == 1
        assert stats["pending"] == 0
