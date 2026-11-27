"""BDD: manifest de verificación con commit/revision/resultados y stale (F1c)."""

from __future__ import annotations

from fast_mcp.features.delta_tracking.domain.spec_service import (
    verification_is_stale as spec_verification_is_stale,
)
from fast_mcp.features.tasks.handlers import verification as verification_handlers
from fast_mcp.features.tasks.handlers.verification import (
    build_manifest,
    manifest_is_stale,
    record_verification,
)


def test_build_manifest_incluye_commit_revision_resultados():
    m = build_manifest(
        passed=True,
        level="predicates",
        runner="verify_predicates",
        task_id="SPEC-X",
        results=[{"criterion": "c", "passed": True}],
        commit="abc123",
        revision="abc123",
    )

    assert m["commit"] == "abc123"
    assert m["revision"] == "abc123"
    assert m["all_passed"] is True
    assert m["results"][0]["criterion"] == "c"


def test_manifest_is_stale_detecta_otro_commit():
    m = {"commit": "abc", "revision": "abc"}
    assert manifest_is_stale(m, "def") is True
    assert manifest_is_stale(m, "abc") is False


def test_manifest_is_stale_detecta_otra_revision():
    m = {"commit": "abc", "revision": "r1"}
    assert manifest_is_stale(m, "abc", "r2") is True
    assert manifest_is_stale(m, "abc", "r1") is False


def test_manifest_vacio_o_sin_manifest_es_stale():
    assert manifest_is_stale(None) is True
    assert manifest_is_stale({}) is True


def test_spec_service_verification_is_stale():
    m = {"commit": "abc", "revision": "r1"}
    assert spec_verification_is_stale(m, "def") is True
    assert spec_verification_is_stale(m, "abc") is False


def test_record_verification_persiste_manifest_en_trace(monkeypatch):
    from fast_mcp.features.tasks.handlers import _di

    events = []

    def spy(chain_dir, ev):
        events.append(ev)
        return True

    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda cd: None)
    monkeypatch.setattr(_di, "push_trace", spy)
    monkeypatch.setattr(_di, "_KNOWLEDGE_WRITER", None)

    ctx = _ctx("/tmp/proj")

    record_verification(
        ctx,
        session_id="S-1",
        passed=True,
        level="predicates",
        runner="verify_predicates",
        task_id="T-1",
        results=[{"criterion": "c", "passed": True}],
        commit="abc",
        revision="abc",
    )

    assert events and events[-1]["data"].get("manifest", {}).get("commit") == "abc"
    assert events[-1]["data"]["manifest"]["results"][0]["criterion"] == "c"


def _ctx(chain_dir):
    from types import SimpleNamespace

    return SimpleNamespace(chain_dir=chain_dir, task_id="T-1", session_id="S-1", cpg_adapter=None)
