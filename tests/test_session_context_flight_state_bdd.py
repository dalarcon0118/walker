"""test_session_context_flight_state_bdd.py — flight_state projection."""

from types import SimpleNamespace

import pytest

from fast_mcp.memories.handlers import knowledge


def _ctx():
    class _Ctx:
        chain_dir = "/tmp/proyecto"
        project_id = "proyecto"

        def code_context_service(self):
            return SimpleNamespace(
                execute=lambda target_file, query: {
                    "symbols": [{"name": "f"}],
                    "relations": {"calls": [], "called_by": [], "called_by_total": 0},
                }
            )

    return _Ctx()


def _make_events(sid, *, state_data=None, metrics_data=None, include_state=True, include_metrics=True):
    events = []
    if include_metrics and metrics_data is not None:
        events.append(SimpleNamespace(
            event_type="session_metrics", timestamp="2026-01-01T00:00:00",
            task_id="", files=[], data=metrics_data,
            metadata={"session_id": sid},
        ))
    if include_state and state_data is not None:
        events.append(SimpleNamespace(
            event_type="session_state", timestamp="2026-01-01T00:00:01",
            task_id="", files=[], data=state_data,
            metadata={"session_id": sid},
        ))
    return events


@pytest.fixture()
def make(monkeypatch):
    def _setup(events):
        trace_factory = __import__(
            "fast_mcp.features.event_tracing.application.services.trace_factory",
            fromlist=["x"],
        )
        monkeypatch.setattr(
            trace_factory, "build_trace_service",
            lambda cd: SimpleNamespace(load_traces=lambda c: events),
        )

        class _Svc:
            def get_active_spring(self):
                return None

            def list_springs(self):
                return []

            def get_specs(self, sid):
                return []

            def get_session(self, sid):
                return {"session_id": sid, "status": "registered"}

            def get_spring(self, sid):
                return {}

        import fast_mcp.composition as composition

        monkeypatch.setattr(composition, "build_sprint_service", lambda cd: _Svc())
        from fast_mcp.features.cognitive_knowledge.application.services import ckg_read
        from returns.result import Success

        monkeypatch.setattr(
            ckg_read.CkgReadService, "gather_context",
            classmethod(lambda cls, chain_dir, query="": Success([])),
        )
        monkeypatch.setattr(
            ckg_read.CkgReadService, "query_all",
            classmethod(lambda cls, chain_dir: []),
        )
    return _setup


STATE_DATA = {
    "verified": True,
    "verified_at": "2026-11-19T00:00:00Z",
    "verify_level": "cli-marker",
    "dirty_since": "",
    "wrote": True,
    "writes": 1,
    "touched": ["x.txt"],
}

METRICS_DATA = {
    "verified": False,
    "verified_at": "2026-11-18T00:00:00Z",
    "verify_level": "tools",
    "dirty_since": "2026-11-18T00:00:00Z",
    "wrote": True,
    "writes": 2,
    "touched": ["y.txt"],
}


def test_prefers_session_state(make):
    events = _make_events("ses-1", state_data=STATE_DATA, metrics_data=METRICS_DATA)
    make(events)
    out = knowledge.handle_session_context(_ctx(), session_id="ses-1")
    assert out["flight_state"] == STATE_DATA


def test_falls_back_to_session_metrics(make):
    events = _make_events("ses-1", state_data=None, metrics_data=METRICS_DATA, include_state=False)
    make(events)
    out = knowledge.handle_session_context(_ctx(), session_id="ses-1")
    assert out["flight_state"] == METRICS_DATA


def test_returns_empty_when_none(make):
    events = _make_events("ses-1", state_data=None, metrics_data=None, include_state=False, include_metrics=False)
    make(events)
    out = knowledge.handle_session_context(_ctx(), session_id="ses-1")
    assert out["flight_state"] == {}


def test_state_after_metrics_wins(make):
    # state event appended after metrics -> preferred
    events = [
        SimpleNamespace(event_type="session_metrics", timestamp="t", task_id="", files=[],
                        data=METRICS_DATA, metadata={"session_id": "ses-1"}),
        SimpleNamespace(event_type="session_state", timestamp="t2", task_id="", files=[],
                        data=STATE_DATA, metadata={"session_id": "ses-1"}),
    ]
    make(events)
    out = knowledge.handle_session_context(_ctx(), session_id="ses-1")
    assert out["flight_state"] == STATE_DATA
